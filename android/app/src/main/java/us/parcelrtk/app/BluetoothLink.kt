package us.parcelrtk.app

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The receiver link: a Bluetooth Classic (SPP) connection to the paired SparkFun RTK
 * Surveyor. Reads NMEA sentences (checksum-checked) and writes RTCM corrections back
 * down the same link, exactly as the laptop bridge does over its COM port.
 */
class BluetoothLink(private val ctx: Context, private val log: (String) -> Unit) {
    companion object {
        val SPP: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")

        fun nmeaOk(line: String): Boolean {
            if (!line.startsWith("$")) return false
            val star = line.lastIndexOf('*')
            if (star < 0 || star + 3 > line.length) return false
            var c = 0
            for (i in 1 until star) c = c xor line[i].code
            return try { c == line.substring(star + 1, star + 3).toInt(16) } catch (e: NumberFormatException) { false }
        }
    }

    @Volatile var state = "off"          // off | starting | opening | open | silent | dropped | nobt | nodevice | error
    @Volatile var detail = ""
    @Volatile var lastRx = 0L
    @Volatile var txBytes = 0L
    @Volatile var badChecksums = 0
    private val rate = RateMeter()
    private val stop = AtomicBoolean(false)
    private var thread: Thread? = null
    private var out: OutputStream? = null
    private val writeLock = Any()

    @Volatile var deviceName: String = ""
    var onLines: ((List<String>) -> Unit)? = null

    fun nmeaPerSec() = rate.rate()

    @SuppressLint("MissingPermission")
    fun bondedDevices(): List<Pair<String, String>> {
        val bm = ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager ?: return emptyList()
        val ad = bm.adapter ?: return emptyList()
        return try {
            ad.bondedDevices.map { (it.name ?: it.address) to it.address }.sortedBy { it.first }
        } catch (e: SecurityException) { emptyList() }
    }

    fun start(name: String) {
        stopLink()
        deviceName = name
        stop.set(false)
        state = "starting"; detail = ""
        thread = Thread({ run() }, "bt-link").apply { isDaemon = true; start() }
    }

    fun stopLink() {
        stop.set(true)
        try { thread?.interrupt() } catch (_: Exception) {}
        closeSocket()
        thread = null
        state = "off"
    }

    fun write(data: ByteArray): Boolean {
        synchronized(writeLock) {
            val o = out ?: return false
            return try { o.write(data); o.flush(); txBytes += data.size; true } catch (e: IOException) { false }
        }
    }

    private var socket: BluetoothSocket? = null
    private fun closeSocket() {
        synchronized(writeLock) { out = null }
        try { socket?.close() } catch (_: Exception) {}
        socket = null
    }

    @SuppressLint("MissingPermission")
    private fun pickDevice(): BluetoothDevice? {
        val bm = ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        val ad: BluetoothAdapter? = bm?.adapter
        if (ad == null || !ad.isEnabled) { state = "nobt"; detail = "Bluetooth is off — turn it on in the tablet's settings"; return null }
        val bonded = try { ad.bondedDevices } catch (e: SecurityException) { state = "error"; detail = "Bluetooth permission not granted"; return null }
        val want = deviceName.trim()
        val dev = bonded.firstOrNull { (it.name ?: "") == want || it.address == want }
            ?: bonded.firstOrNull { (it.name ?: "").contains("Surveyor", true) || (it.name ?: "").contains("RTK", true) || (it.name ?: "").contains("Facet", true) }
        if (dev == null) {
            state = "nodevice"
            detail = if (want.isEmpty()) "No paired receiver found — pair the Surveyor in the tablet's Bluetooth settings (Classic mode)"
                     else "'$want' is not paired — pair it in the tablet's Bluetooth settings"
        }
        return dev
    }

    @SuppressLint("MissingPermission")
    private fun run() {
        var backoff = 2000L
        while (!stop.get()) {
            val dev = pickDevice()
            if (dev == null) { sleep(5000); continue }
            state = "opening"; detail = "connecting to ${dev.name ?: dev.address}…"
            val sock = try {
                dev.createRfcommSocketToServiceRecord(SPP)
            } catch (e: Exception) { state = "error"; detail = e.message ?: "cannot create socket"; sleep(backoff); continue }
            try {
                sock.connect()
            } catch (e: IOException) {
                try { sock.close() } catch (_: Exception) {}
                state = "noresponse"
                detail = "${dev.name ?: dev.address} did not answer — is the receiver on, in Rover mode, Bluetooth set to Classic, and within range?"
                sleep(backoff); backoff = minOf(backoff * 2, 15000)
                continue
            }
            socket = sock
            backoff = 2000
            val input: InputStream
            try {
                input = sock.inputStream
                synchronized(writeLock) { out = sock.outputStream }
            } catch (e: IOException) { closeSocket(); continue }
            state = "open"; detail = dev.name ?: dev.address
            log("connected to receiver ${dev.name ?: dev.address}")
            val buf = ByteArray(4096)
            val sb = StringBuilder()
            try {
                while (!stop.get()) {
                    val n = input.read(buf)
                    if (n < 0) break
                    val lines = ArrayList<String>()
                    for (i in 0 until n) {
                        val ch = buf[i].toInt().toChar()
                        if (ch == '\n') {
                            val s = sb.toString().trim()
                            sb.setLength(0)
                            if (s.startsWith("$")) { if (nmeaOk(s)) lines.add(s) else badChecksums++ }
                        } else if (ch != '\r' && sb.length < 4096) sb.append(ch)
                    }
                    if (lines.isNotEmpty()) {
                        lastRx = System.currentTimeMillis()
                        rate.add(lines.size)
                        onLines?.invoke(lines)
                    }
                }
            } catch (e: IOException) {
                if (!stop.get()) { state = "dropped"; detail = "link dropped — reconnecting"; log("receiver link dropped: ${e.message}") }
            } finally {
                closeSocket()
            }
            if (!stop.get()) sleep(1000)
        }
        state = "off"
    }

    private fun sleep(ms: Long) { try { Thread.sleep(ms) } catch (_: InterruptedException) {} }

    fun status(): Map<String, Any?> {
        val silent = state == "open" && lastRx > 0 && System.currentTimeMillis() - lastRx > 5000
        return mapOf("type" to "bluetooth", "state" to (if (silent) "silent" else state), "detail" to detail,
                     "nmeaPerSec" to Math.round(nmeaPerSec() * 10) / 10.0, "badChecksums" to badChecksums,
                     "txBytes" to txBytes, "port" to deviceName)
    }
}

/** events per second over a sliding window (same as the bridge's RateMeter) */
class RateMeter(private val windowMs: Long = 5000) {
    private val ev = ArrayDeque<Pair<Long, Int>>()
    @Synchronized fun add(n: Int) {
        val now = System.currentTimeMillis()
        ev.addLast(now to n)
        while (ev.isNotEmpty() && now - ev.first().first > windowMs) ev.removeFirst()
    }
    @Synchronized fun rate(): Double {
        val now = System.currentTimeMillis()
        while (ev.isNotEmpty() && now - ev.first().first > windowMs) ev.removeFirst()
        return ev.sumOf { it.second } / (windowMs / 1000.0)
    }
}
