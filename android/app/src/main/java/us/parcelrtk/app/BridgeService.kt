package us.parcelrtk.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Binder
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject
import java.util.ArrayDeque

/**
 * The tablet's version of rtk_bridge.py: keeps the receiver link and the correction stream
 * alive in the background (as a foreground service with a notification) and hands
 * everything to the web app in MainActivity.
 */
class BridgeService : Service() {
    inner class LocalBinder : Binder() { val service get() = this@BridgeService }
    private val binder = LocalBinder()

    lateinit var prefs: SharedPreferences
    lateinit var bt: BluetoothLink
    var ntrip: NtripClient? = null
    @Volatile var lastGga: String? = null
    @Volatile var lastFix: JSONObject? = null
    val logLines = ArrayDeque<String>()
    var listener: Listener? = null

    interface Listener {
        fun onNmea(lines: List<String>)
        fun onLog(line: String)
    }

    fun log(msg: String, tag: String = "bridge") {
        val line = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US).format(java.util.Date()) + " [$tag] $msg"
        synchronized(logLines) { logLines.addLast(line); while (logLines.size > 400) logLines.removeFirst() }
        listener?.onLog(line)
    }

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences("prtk", Context.MODE_PRIVATE)
        bt = BluetoothLink(this) { log(it, "rx") }
        bt.onLines = { lines ->
            for (ln in lines) if (ln.length > 6 && ln.substring(3, 6) == "GGA") {
                val f = ln.split("*")[0].split(",")
                val q = f.getOrNull(6)?.toIntOrNull() ?: 0
                if (f.size > 9 && q > 0 && f[2].isNotEmpty()) lastGga = ln
                lastFix = JSONObject().put("q", q).put("sats", f.getOrNull(7) ?: "").put("hdop", f.getOrNull(8) ?: "")
            }
            listener?.onNmea(lines)
        }
        startForeground(1, notification())
        applyConfig()
    }

    override fun onBind(intent: Intent?): IBinder = binder
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onDestroy() { stopIo(); super.onDestroy() }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(NotificationChannel("link", getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW))
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "link") else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle(getString(R.string.notif_title)).setContentText("Receiver link and corrections")
            .setSmallIcon(R.drawable.ic_stat).setContentIntent(pi).setOngoing(true).build()
    }

    // ---- configuration (mirrors the laptop's rtk_config.json, minus the web/update parts) ----
    fun config(): JSONObject = JSONObject()
        .put("source", JSONObject().put("type", "bluetooth").put("device", prefs.getString("device", "") ?: ""))
        .put("ntrip", JSONObject().put("enabled", prefs.getBoolean("ntripOn", false)).put("host", prefs.getString("host", "") ?: "")
            .put("port", prefs.getInt("port", 2101)).put("mount", prefs.getString("mount", "") ?: "").put("user", prefs.getString("user", "") ?: "")
            .put("pass", "").put("passSet", (prefs.getString("pass", "") ?: "").isNotEmpty()).put("ggaInterval", prefs.getInt("gga", 10)))

    fun setConfig(cfg: JSONObject) {
        val e = prefs.edit()
        cfg.optJSONObject("source")?.let { s -> if (s.has("device")) e.putString("device", s.optString("device")) }
        cfg.optJSONObject("ntrip")?.let { n ->
            if (n.has("enabled")) e.putBoolean("ntripOn", n.optBoolean("enabled"))
            if (n.has("host")) e.putString("host", n.optString("host"))
            if (n.has("port")) e.putInt("port", n.optInt("port", 2101))
            if (n.has("mount")) e.putString("mount", n.optString("mount"))
            if (n.has("user")) e.putString("user", n.optString("user"))
            if (n.optBoolean("clearPass")) e.putString("pass", "")
            else if (n.optString("pass").isNotEmpty()) e.putString("pass", n.optString("pass"))   // blank keeps the saved one
            if (n.has("ggaInterval")) e.putInt("gga", n.optInt("ggaInterval", 10))
        }
        e.apply()
        log("settings saved")
        Thread { stopIo(); applyConfig() }.start()
    }

    private fun applyConfig() {
        bt.start(prefs.getString("device", "") ?: "")
        if (prefs.getBoolean("ntripOn", false) && !(prefs.getString("host", "") ?: "").isEmpty() && !(prefs.getString("mount", "") ?: "").isEmpty()) {
            ntrip = NtripClient(prefs.getString("host", "")!!, prefs.getInt("port", 2101), prefs.getString("mount", "")!!,
                prefs.getString("user", "") ?: "", prefs.getString("pass", "") ?: "", prefs.getInt("gga", 10),
                { lastGga }, { bt.write(it) }, { log(it, "ntrip") }).also { it.start() }
        }
    }

    private fun stopIo() {
        ntrip?.stopClient(); ntrip = null
        bt.stopLink()
    }

    fun status(): JSONObject {
        val n = ntrip
        return JSONObject().put("t", "status").put("rx", JSONObject(bt.status())).put("ntrip", if (n != null) JSONObject(n.status()) else JSONObject.NULL)
            .put("fix", lastFix ?: JSONObject.NULL).put("viewers", 1)
    }

    fun devices(): JSONArray = JSONArray().also { arr -> bt.bondedDevices().forEach { arr.put(JSONObject().put("name", it.first).put("address", it.second)) } }
    fun logJson(): JSONArray = JSONArray().also { arr -> synchronized(logLines) { logLines.forEach { arr.put(it) } } }
}
