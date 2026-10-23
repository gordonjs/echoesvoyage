package us.parcelrtk.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.webkit.WebViewAssetLoader
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Hosts the Parcel RTK web app (packaged from the repo's web/ folder) and connects it to
 * the BridgeService through a small JavaScript interface, window.PRTKNative.
 */
class MainActivity : AppCompatActivity(), BridgeService.Listener {
    private lateinit var web: WebView
    private lateinit var tiles: TileCache
    private var svc: BridgeService? = null
    private val main = Handler(Looper.getMainLooper())
    private val pendingNmea = ArrayList<String>()
    private var flushQueued = false
    private var pendingSave: Pair<String, String>? = null      // (mime, text) waiting for a file picker

    private val conn = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            svc = (binder as BridgeService.LocalBinder).service
            svc?.listener = this@MainActivity
            pushConfig()
        }
        override fun onServiceDisconnected(name: ComponentName?) { svc = null }
    }

    private val askPerms = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { startBridge() }
    private val saveFile = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri: Uri? ->
        val p = pendingSave; pendingSave = null
        if (uri != null && p != null) {
            try { contentResolver.openOutputStream(uri)?.use { it.write(p.second.toByteArray()) }; js("PRTK.app.toast('Saved')") }
            catch (e: Exception) { js("PRTK.app.toast(${q("Could not save: " + e.message)}, true)") }
        }
    }
    private val openFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) {
            try {
                val text = contentResolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
                js("PRTK.app.native.onProjectFile(${q(text)})")
            } catch (e: Exception) { js("PRTK.app.toast(${q("Could not read the file: " + e.message)}, true)") }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        tiles = TileCache(File(filesDir, "tiles"))
        web = WebView(this)
        setContentView(web)
        web.setBackgroundColor(0xFF0F1419.toInt())
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mediaPlaybackRequiresUserGesture = false
            setSupportZoom(false)
            builtInZoomControls = false
            useWideViewPort = true
            loadWithOverviewMode = true
        }
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                val u = request.url
                if (u.host == "appassets.androidplatform.net") {
                    val path = u.path ?: ""
                    if (path.startsWith("/assets/web/tiles/")) return tiles.respond(path.removePrefix("/assets/web"))
                    return loader.shouldInterceptRequest(u)
                }
                return null
            }
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val u = request.url
                if (u.host == "appassets.androidplatform.net") return false
                try { startActivity(Intent(Intent.ACTION_VIEW, u)) } catch (_: Exception) {}
                return true
            }
        }
        web.addJavascriptInterface(Native(), "PRTKNative")
        web.loadUrl("https://appassets.androidplatform.net/assets/web/index.html")
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        requestPermissionsThenStart()
    }

    private fun requestPermissionsThenStart() {
        val need = ArrayList<String>()
        if (Build.VERSION.SDK_INT >= 31 && ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
            need.add(Manifest.permission.BLUETOOTH_CONNECT)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            need.add(Manifest.permission.POST_NOTIFICATIONS)
        if (need.isEmpty()) startBridge() else askPerms.launch(need.toTypedArray())
    }

    private fun startBridge() {
        val i = Intent(this, BridgeService::class.java)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
        bindService(i, conn, Context.BIND_AUTO_CREATE)
    }

    override fun onDestroy() {
        svc?.listener = null
        try { unbindService(conn) } catch (_: Exception) {}
        if (isFinishing) stopService(Intent(this, BridgeService::class.java))
        super.onDestroy()
    }

    // ---- service -> web app --------------------------------------------------------
    override fun onNmea(lines: List<String>) {
        synchronized(pendingNmea) { pendingNmea.addAll(lines); if (pendingNmea.size > 200) pendingNmea.subList(0, pendingNmea.size - 200).clear() }
        if (!flushQueued) {
            flushQueued = true
            main.postDelayed({ flushQueued = false; flushNmea() }, 120)
        }
    }
    private fun flushNmea() {
        val arr = JSONArray()
        synchronized(pendingNmea) { pendingNmea.forEach { arr.put(it) }; pendingNmea.clear() }
        if (arr.length() > 0) js("PRTK.app.native.onNmea($arr)")
    }
    override fun onLog(line: String) { js("PRTK.app.native.onLog(${q(line)})") }

    private val statusTick = object : Runnable {
        override fun run() {
            svc?.let { js("PRTK.app.native.onStatus(${it.status()})") }
            main.postDelayed(this, 1000)
        }
    }
    override fun onResume() { super.onResume(); main.post(statusTick) }
    override fun onPause() { super.onPause(); main.removeCallbacks(statusTick) }

    private fun pushConfig() { svc?.let { js("PRTK.app.native.onConfig(${it.config()}, ${it.devices()})") } }
    private fun js(code: String) { main.post { try { web.evaluateJavascript(code, null) } catch (_: Exception) {} } }
    private fun q(s: String): String = JSONObject.quote(s)

    // ---- web app -> service (called on a WebView thread) ---------------------------
    inner class Native {
        @JavascriptInterface fun version(): String = BuildConfig.VERSION_NAME
        @JavascriptInterface fun ready() { main.post { pushConfig() } }
        @JavascriptInterface fun setConfig(json: String) { try { svc?.setConfig(JSONObject(json)); main.postDelayed({ pushConfig() }, 300) } catch (e: Exception) { js("PRTK.app.toast(${q("Bad settings: " + e.message)}, true)") } }
        @JavascriptInterface fun refreshDevices() { main.post { pushConfig() } }
        @JavascriptInterface fun getLog(): String = (svc?.logJson() ?: JSONArray()).toString()
        @JavascriptInterface fun saveFile(name: String, text: String, mime: String) {
            pendingSave = mime to text
            main.post { saveFile.launch(name) }
        }
        @JavascriptInterface fun openProjectFile() { main.post { openFile.launch(arrayOf("application/json", "text/plain", "*/*")) } }
        @JavascriptInterface fun keepScreenOn(on: Boolean) {
            main.post { if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
        }
        @JavascriptInterface fun openBluetoothSettings() { main.post { try { startActivity(Intent(android.provider.Settings.ACTION_BLUETOOTH_SETTINGS)) } catch (_: Exception) {} } }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // let the web app close its panel first; only leave the app when it has nothing open
        web.evaluateJavascript("(function(){ if (PRTK.app.modal.isOpen()) { PRTK.app.modal.close(); return true; } if (PRTK.app.isPanelOpen()) { PRTK.app.closePanel(); return true; } return false; })()") { r ->
            if (r != "true") moveTaskToBack(true)
        }
    }
}
