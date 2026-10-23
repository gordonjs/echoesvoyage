package us.parcelrtk.app

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.Base64
import java.util.concurrent.atomic.AtomicBoolean

/**
 * NTRIP v2 client (a port of the laptop bridge's): connects to the caster, sends the
 * receiver's position (GGA) so a network service returns corrections for this area, and
 * hands every byte of RTCM to the receiver link. Reconnects with backoff.
 */
class NtripClient(
    private val host: String, private val port: Int, private val mount: String,
    private val user: String, private val pass: String, private val ggaIntervalSec: Int,
    private val lastGga: () -> String?,
    private val toReceiver: (ByteArray) -> Boolean,
    private val log: (String) -> Unit
) {
    @Volatile var state = "starting"       // starting | connecting | streaming | refused | error | stopped
    @Volatile var detail = ""
    @Volatile var total = 0L
    @Volatile var since = 0L
    private val rate = RateMeter()
    private val scanner = RtcmScanner()
    private val stop = AtomicBoolean(false)
    private var sock: Socket? = null
    private var thread: Thread? = null

    fun start() { thread = Thread({ run() }, "ntrip").apply { isDaemon = true; start() } }
    fun stopClient() { stop.set(true); try { sock?.close() } catch (_: Exception) {}; state = "stopped" }

    fun status(): Map<String, Any?> = mapOf(
        "state" to state, "detail" to detail, "host" to host, "mount" to mount,
        "bytesPerSec" to Math.round(rate.rate()), "total" to total, "format" to scanner.describe(),
        "types" to scanner.types().take(24), "badFrames" to scanner.bad, "base" to scanner.base(),
        "upSec" to (if (since > 0) (System.currentTimeMillis() - since) / 1000 else 0)
    )

    private fun run() {
        var backoff = 2000L
        while (!stop.get()) {
            try {
                session()
                backoff = 2000L
            } catch (e: Refused) {
                state = "refused"; detail = e.message ?: "refused"
                log("caster refused: ${e.message}")
                sleep(30000)
            } catch (e: Exception) {
                if (stop.get()) break
                state = "error"; detail = e.message ?: e.javaClass.simpleName
                log("ntrip: $detail — retrying in ${backoff / 1000}s")
                sleep(backoff); backoff = minOf(backoff * 2, 60000L)
            } finally { since = 0 }
        }
        state = "stopped"
    }
    private class Refused(msg: String) : Exception(msg)

    private fun request(gga: String?): ByteArray {
        val lines = mutableListOf("GET /$mount HTTP/1.1", "Host: $host:$port", "Ntrip-Version: Ntrip/2.0",
                                  "User-Agent: NTRIP ParcelRTK-Android/2.1", "Connection: close")
        if (user.isNotEmpty()) lines.add("Authorization: Basic " + Base64.getEncoder().encodeToString("$user:$pass".toByteArray()))
        if (gga != null) lines.add("Ntrip-GGA: $gga")
        return (lines.joinToString("\r\n") + "\r\n\r\n").toByteArray()
    }

    private fun session() {
        state = "connecting"; detail = "$host:$port/$mount"
        val gga = lastGga()
        val s = Socket()
        sock = s
        try {
            s.connect(InetSocketAddress(host, port), 15000)
            s.soTimeout = 15000
            val out = s.getOutputStream()
            out.write(request(gga)); out.flush()
            val input = s.getInputStream()
            // read the status line + headers
            val head = ByteArrayOutputStream()
            var statusLine: String? = null
            val headers = HashMap<String, String>()
            var rest = ByteArray(0)
            val one = ByteArray(1)
            while (true) {
                val n = input.read(one)
                if (n < 0) throw IOException("caster closed the connection before answering")
                head.write(one[0].toInt())
                val text = head.toString(Charsets.ISO_8859_1.name())
                if (statusLine == null && text.endsWith("\r\n")) {
                    statusLine = text.trim()
                    if (statusLine.startsWith("ICY")) break        // ICY casters: headers may be absent
                    head.reset()
                } else if (statusLine != null && text.endsWith("\r\n\r\n")) {
                    for (h in text.split("\r\n")) {
                        val i = h.indexOf(':'); if (i > 0) headers[h.substring(0, i).trim().lowercase()] = h.substring(i + 1).trim()
                    }
                    break
                } else if (statusLine != null && text.endsWith("\r\n") && text.trim().isEmpty()) {
                    break
                }
                if (head.size() > 16000) throw IOException("bad caster answer")
            }
            val st = statusLine ?: throw IOException("no answer")
            if (st.startsWith("SOURCETABLE")) throw Refused("mountpoint '$mount' does not exist on this caster")
            if (st.contains(" 401") || st.contains(" 403")) throw Refused("$st — check the username, password, and that your plan includes '$mount'")
            if (!(st.startsWith("ICY 200") || Regex("HTTP/1\\.[01] 200").containsMatchIn(st))) throw IOException("caster answered: ${st.take(80)}")
            val dech = if ((headers["transfer-encoding"] ?: "").contains("chunked", true)) Dechunker() else null
            state = "streaming"; detail = "$host/$mount"; since = System.currentTimeMillis()
            log("streaming corrections from $host/$mount" + (if (dech != null) " (chunked)" else ""))
            s.soTimeout = 1000
            var lastGgaSent = if (gga != null) System.currentTimeMillis() else 0L
            if (gga != null) { out.write((gga + "\r\n").toByteArray()); out.flush() }
            var lastData = System.currentTimeMillis()
            var warned = false
            val buf = ByteArray(8192)
            while (!stop.get()) {
                var n = -2
                try { n = input.read(buf) } catch (e: SocketTimeoutException) { n = -2 }
                if (n == -1) throw IOException("caster closed the connection")
                val now = System.currentTimeMillis()
                if (n > 0) {
                    val data = buf.copyOf(n)
                    val payload = dech?.feed(data) ?: data
                    if (payload.isNotEmpty()) {
                        lastData = now
                        total += payload.size
                        rate.add(payload.size)
                        scanner.feed(payload)
                        toReceiver(payload)
                        if (!warned && total > 3000 && scanner.describe() == "SPARTN") {
                            warned = true
                            log("this mountpoint sends SPARTN, which the receiver ignores over Bluetooth — use an RTCM mountpoint (PointPerfect: NEAR-RTCM)")
                        }
                    }
                }
                if (now - lastData > 30000) throw IOException("no data from the caster for 30 s")
                val g = lastGga()
                if (g != null && now - lastGgaSent >= maxOf(2, ggaIntervalSec) * 1000L) {
                    out.write((g + "\r\n").toByteArray()); out.flush()
                    lastGgaSent = now
                }
            }
        } finally {
            sock = null
            try { s.close() } catch (_: Exception) {}
        }
    }

    private fun sleep(ms: Long) { try { Thread.sleep(ms) } catch (_: InterruptedException) {} }
}

/** HTTP/1.1 chunked transfer decoding, byte for byte like the bridge's Dechunker. */
class Dechunker {
    private var buf = ByteArrayOutputStream()
    private var state = "SIZE"
    private var need = 0
    fun feed(data: ByteArray): ByteArray {
        buf.write(data)
        var b = buf.toByteArray()
        val out = ByteArrayOutputStream()
        while (b.isNotEmpty()) {
            when (state) {
                "SIZE" -> {
                    val i = indexOfCrlf(b)
                    if (i < 0) { if (b.size > 64) throw IOException("bad chunk header"); break }
                    val line = String(b, 0, i, Charsets.ISO_8859_1).split(";")[0].trim()
                    b = b.copyOfRange(i + 2, b.size)
                    if (line.isEmpty()) continue
                    need = line.toInt(16)
                    state = if (need > 0) "DATA" else "END"
                }
                "DATA" -> {
                    val take = minOf(need, b.size)
                    out.write(b, 0, take)
                    b = b.copyOfRange(take, b.size)
                    need -= take
                    if (need == 0) state = "CRLF"
                }
                "CRLF" -> { if (b.size < 2) break; b = b.copyOfRange(2, b.size); state = "SIZE" }
                else -> b = ByteArray(0)
            }
        }
        buf = ByteArrayOutputStream().also { it.write(b) }
        return out.toByteArray()
    }
    private fun indexOfCrlf(b: ByteArray): Int { for (i in 0 until b.size - 1) if (b[i] == 13.toByte() && b[i + 1] == 10.toByte()) return i; return -1 }
}

/**
 * Looks at the correction stream: counts RTCM 3 frames by type (CRC-24Q checked), decodes
 * the base position from message 1005/1006, and recognises SPARTN so the app can warn.
 */
class RtcmScanner {
    private var buf = ByteArray(0)
    private val counts = HashMap<Int, Int>()
    @Volatile var bad = 0
    @Volatile private var spartn = 0
    @Volatile private var basePos: Map<String, Any?>? = null

    fun types(): List<Int> = synchronized(counts) { counts.keys.sorted() }
    fun describe(): String = synchronized(counts) { if (counts.isNotEmpty() && counts.values.sum() >= spartn) "RTCM3" else if (spartn > 0) "SPARTN" else "unknown" }
    fun base(): Map<String, Any?>? = basePos

    fun feed(data: ByteArray) {
        buf += data
        var i = 0
        while (i < buf.size) {
            val b = buf[i].toInt() and 0xFF
            if (b == 0xD3) {
                if (i + 3 > buf.size) break
                val len = ((buf[i + 1].toInt() and 0x03) shl 8) or (buf[i + 2].toInt() and 0xFF)
                val end = i + 3 + len + 3
                if (end > buf.size) break
                val frame = buf.copyOfRange(i, i + 3 + len)
                val crc = ((buf[end - 3].toInt() and 0xFF) shl 16) or ((buf[end - 2].toInt() and 0xFF) shl 8) or (buf[end - 1].toInt() and 0xFF)
                if (crc24q(frame) == crc) {
                    val type = ((buf[i + 3].toInt() and 0xFF) shl 4) or ((buf[i + 4].toInt() and 0xF0) shr 4)
                    synchronized(counts) { counts[type] = (counts[type] ?: 0) + 1 }
                    if (type == 1005 || type == 1006) decodeBase(buf.copyOfRange(i + 3, i + 3 + len))
                    i = end
                    continue
                }
                bad++
                i++
                continue
            }
            if (b == 0x73 && i + 1 < buf.size) spartn++
            i++
        }
        buf = buf.copyOfRange(i, buf.size)
        if (buf.size > 4096) buf = buf.copyOfRange(buf.size - 1024, buf.size)
    }

    private fun getBits(b: ByteArray, pos: Int, n: Int): Long {
        var v = 0L
        for (i in pos until pos + n) v = (v shl 1) or (((b[i shr 3].toInt() shr (7 - (i and 7))) and 1).toLong())
        return v
    }
    private fun getSBits(b: ByteArray, pos: Int, n: Int): Long { val v = getBits(b, pos, n); return if (v and (1L shl (n - 1)) != 0L) v - (1L shl n) else v }

    private fun decodeBase(p: ByteArray) {
        if (p.size < 19) return
        val station = getBits(p, 12, 12).toInt()
        val x = getSBits(p, 34, 38) * 1e-4
        val y = getSBits(p, 74, 38) * 1e-4
        val z = getSBits(p, 114, 38) * 1e-4
        // ECEF -> geodetic (WGS84)
        val a = 6378137.0; val f = 1 / 298.257223563; val e2 = f * (2 - f)
        val lon = Math.atan2(y, x); val pr = Math.hypot(x, y)
        var lat = Math.atan2(z, pr * (1 - e2)); var h = 0.0
        repeat(8) {
            val sp = Math.sin(lat); val N = a / Math.sqrt(1 - e2 * sp * sp)
            h = pr / Math.cos(lat) - N
            lat = Math.atan2(z, pr * (1 - e2 * N / (N + h)))
        }
        basePos = mapOf("station" to station, "lat" to Math.toDegrees(lat), "lon" to Math.toDegrees(lon), "h" to h,
                        "ecef" to listOf(x, y, z))
    }

    companion object {
        private val TABLE = IntArray(256).also { t ->
            for (i in 0 until 256) {
                var c = i shl 16
                repeat(8) { c = c shl 1; if (c and 0x1000000 != 0) c = c xor 0x1864CFB }
                t[i] = c and 0xFFFFFF
            }
        }
        fun crc24q(data: ByteArray): Int {
            var crc = 0
            for (b in data) crc = ((crc shl 8) and 0xFFFFFF) xor TABLE[((crc shr 16) xor (b.toInt() and 0xFF)) and 0xFF]
            return crc
        }
    }
}
