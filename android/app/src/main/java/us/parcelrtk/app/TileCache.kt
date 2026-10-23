package us.parcelrtk.app

import android.webkit.WebResourceResponse
import java.io.ByteArrayInputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Map tiles, cached on the tablet so the satellite view keeps working without internet
 * once you have looked at an area (the same idea as the laptop's rtk_data/tiles).
 * The web app asks for tiles/{layer}/{z}/{x}/{y}; this answers them.
 */
class TileCache(private val root: File) {
    private val layers = mapOf(
        "sat" to "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "topo" to "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        "street" to "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    )
    private val negative = HashMap<String, Long>()
    private val re = Regex("^/tiles/([a-z]+)/(\\d{1,2})/(\\d{1,8})/(\\d{1,8})(?:\\.\\w+)?$")

    fun matches(path: String) = re.matches(path)

    fun respond(path: String): WebResourceResponse {
        val m = re.find(path) ?: return notFound()
        val (layer, zs, xs, ys) = m.destructured
        val z = zs.toInt(); val x = xs.toLong(); val y = ys.toLong()
        val tpl = layers[layer]
        if (tpl == null || z !in 0..22 || x < 0 || y < 0 || x >= (1L shl z) || y >= (1L shl z)) return notFound()
        val f = File(root, "$layer/$z/$x/$y")
        if (f.isFile) return ok(f.readBytes())
        val key = "$layer/$z/$x/$y"
        synchronized(negative) { if ((negative[key] ?: 0) > System.currentTimeMillis()) return notFound() }
        val data = try {
            val c = URL(tpl.replace("{z}", "$z").replace("{x}", "$x").replace("{y}", "$y")).openConnection() as HttpURLConnection
            c.connectTimeout = 8000; c.readTimeout = 8000
            c.setRequestProperty("User-Agent", "ParcelRTK-Android/2.1 (personal property map)")
            val bytes = c.inputStream.use { it.readBytes() }
            c.disconnect()
            if (bytes.size < 4 || !((bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte()) || (bytes[0] == 0x89.toByte() && bytes[1] == 'P'.code.toByte()))) null else bytes
        } catch (e: Exception) { null }
        if (data == null) {
            synchronized(negative) { negative[key] = System.currentTimeMillis() + 60000 }
            return notFound()
        }
        try {
            f.parentFile?.mkdirs()
            val tmp = File(f.path + ".part")
            tmp.writeBytes(data)
            tmp.renameTo(f)
        } catch (_: Exception) {}
        return ok(data)
    }

    private fun ok(bytes: ByteArray): WebResourceResponse {
        val mime = if (bytes[0] == 0xFF.toByte()) "image/jpeg" else "image/png"
        return WebResourceResponse(mime, null, 200, "OK", mapOf("Cache-Control" to "max-age=604800", "Access-Control-Allow-Origin" to "*"), ByteArrayInputStream(bytes))
    }
    private fun notFound() = WebResourceResponse("text/plain", "utf-8", 404, "Not Found", emptyMap(), ByteArrayInputStream(ByteArray(0)))
}
