package ichiyon.nethernet;

import com.google.gson.Gson;
import dev.kastle.netty.channel.nethernet.signaling.NetherNetSignaling.IceServerInfo;
import dev.kastle.webrtc.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.logging.*;

/** Structured, bounded diagnostics. Never serialize SDP, credentials or raw stats. */
public final class Diagnostics {
    private static final Gson JSON = new Gson();
    private static final String BOOT = UUID.randomUUID().toString();
    private static final String SALT = UUID.randomUUID().toString();
    private static final long START = System.nanoTime();
    private static long duration;
    private static FileHandler output;
    static {
        String directory = System.getProperty("ichiyon.nethernet.diagnostics");
        if (directory != null) {
            try {
                duration = Long.parseLong(System.getProperty("ichiyon.nethernet.seconds", "86400"));
                if (duration < 1 || duration > 86400) throw new IllegalArgumentException();
                Path dir = Path.of(directory);
                Files.createDirectories(dir);
                Files.setPosixFilePermissions(dir, PosixFilePermissions.fromString("rwx------"));
                output = new FileHandler(dir.resolve("events-%g.jsonl").toString(), 2 * 1024 * 1024, 4, true);
                output.setFormatter(new java.util.logging.Formatter() {
                    public String format(LogRecord record) { return record.getMessage() + "\n"; }
                });
                event(0, "diagnostics_ready");
            } catch (Exception ignored) {
                // Fixed message only: exception messages can include paths or secrets.
                System.err.println("NETHER_DIAGNOSTICS_UNAVAILABLE");
            }
        }
    }

    private static boolean enabled() {
        return output != null && (System.nanoTime() - START) / 1_000_000_000 < duration;
    }
    private static synchronized void emit(long connection, String event, Map<String, Object> fields) {
        if (!enabled()) return;
        try {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("schema", 1); row.put("utc", Instant.now().toString());
            row.put("boot", BOOT); row.put("connection", Long.toUnsignedString(connection));
            row.put("event", event); row.putAll(fields);
            output.publish(new LogRecord(Level.INFO, JSON.toJson(row)));
            output.flush();
        } catch (RuntimeException ignored) { /* Diagnostics must not interrupt a connection. */ }
    }
    public static void event(long id, String event) { emit(id, event, Map.of()); }
    public static void state(long id, String event, Enum<?> state) {
        emit(id, event, Map.of("state", state.name()));
    }
    private static String opaque(Object value) {
        if (value == null) return "unknown";
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest((SALT + value).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes, 0, 10);
        } catch (Exception ignored) { return "unknown"; }
    }
    private static String choice(Object value, String... allowed) {
        String text = String.valueOf(value).toLowerCase(Locale.ROOT);
        for (String item : allowed) if (item.equals(text)) return item;
        return "unknown";
    }
    private static String family(String address) { return address.contains(":") ? "ipv6" : address.matches("[0-9.]+") ? "ipv4" : "hostname"; }

    public static void signal(String direction, String raw) {
        if (!enabled()) return;
        try {
            String[] parts = raw.split(" ", 3);
            if (parts.length < 2) return;
            String type = choice(parts[0], "connectrequest", "connectresponse", "candidateadd", "connecterror");
            if (type.equals("unknown")) return;
            long id = Long.parseUnsignedLong(parts[1]);
            emit(id, "signal", Map.of("direction", direction, "type", type));
            if (parts.length == 3) {
                // Offers may carry candidates in SDP as well as trickled CANDIDATEADD.
                for (String line : parts[2].split("\\r?\\n")) {
                    if (line.startsWith("a=candidate:")) line = line.substring(2);
                    if (line.startsWith("candidate:")) candidate(id, direction, line);
                }
            }
        } catch (RuntimeException ignored) { event(0, "diagnostic_signal_parse_failed"); }
    }
    private static void candidate(long id, String direction, String line) {
        String[] c = line.trim().split("\\s+");
        if (c.length < 8 || !c[6].equals("typ")) return;
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("direction", direction);
        fields.put("type", choice(c[7], "host", "srflx", "prflx", "relay"));
        fields.put("protocol", choice(c[2], "udp", "tcp"));
        fields.put("address_family", family(c[4]));
        fields.put("endpoint", opaque(c[4] + ":" + c[5]));
        fields.put("port", Integer.parseInt(c[5]));
        emit(id, "candidate", fields);
    }
    public static void servers(List<IceServerInfo> servers) {
        if (!enabled()) return;
        int urls = 0;
        for (IceServerInfo server : servers) urls += server.urls().size();
        emit(0, "ice_servers", Map.of("servers", servers.size(), "urls", urls));
    }
    public static void iceError(long id, RTCPeerConnectionIceErrorEvent error) {
        if (!enabled()) return;
        emit(id, "ice_candidate_error", Map.of("code", error.getErrorCode(),
            "server", opaque(error.getUrl())));
    }
    public static void stats(long id, RTCPeerConnection pc) {
        if (!enabled() || pc == null) return;
        try { pc.getStats(report -> report(id, report)); }
        catch (RuntimeException ignored) { event(id, "stats_unavailable"); }
    }
    public static void report(long id, RTCStatsReport report) {
        if (!enabled()) return;
        try {
            Set<String> selected = new HashSet<>();
            for (RTCStats stat : report.getStats().values()) {
                if (stat.getType() == RTCStatsType.TRANSPORT) {
                    Object pair = stat.getAttributes().get("selectedCandidatePairId");
                    if (pair != null) selected.add(pair.toString());
                }
            }
            String snapshot = opaque(report.getTimestamp());
            for (RTCStats stat : report.getStats().values()) {
                RTCStatsType type = stat.getType();
                if (type != RTCStatsType.CANDIDATE_PAIR && type != RTCStatsType.LOCAL_CANDIDATE
                        && type != RTCStatsType.REMOTE_CANDIDATE) continue;
                Map<String, Object> a = stat.getAttributes(), f = new LinkedHashMap<>();
                f.put("snapshot", snapshot); f.put("id", opaque(stat.getId()));
                f.put("stats_type", type.name());
                if (type == RTCStatsType.CANDIDATE_PAIR) {
                    f.put("selected", selected.contains(stat.getId()));
                    f.put("local", opaque(a.get("localCandidateId")));
                    f.put("remote", opaque(a.get("remoteCandidateId")));
                    f.put("state", choice(a.get("state"), "frozen", "waiting", "in-progress", "failed", "succeeded"));
                    for (String key : List.of("nominated", "bytesSent", "bytesReceived", "requestsSent",
                            "requestsReceived", "responsesSent", "responsesReceived", "currentRoundTripTime")) {
                        Object v = a.get(key);
                        if (v instanceof Boolean || v instanceof Number) f.put(key, v);
                    }
                } else {
                    f.put("candidate_type", choice(a.get("candidateType"), "host", "srflx", "prflx", "relay"));
                    f.put("protocol", choice(a.get("protocol"), "udp", "tcp"));
                    f.put("relay_protocol", choice(a.get("relayProtocol"), "udp", "tcp", "tls"));
                    Object address = a.getOrDefault("address", a.get("ip"));
                    f.put("address_family", address == null ? "unknown" : family(address.toString()));
                    f.put("endpoint", opaque(address == null ? null : address + ":" + a.get("port")));
                    if (a.get("port") instanceof Number) f.put("port", a.get("port"));
                }
                emit(id, "ice_stats", f);
            }
        } catch (RuntimeException ignored) { event(id, "stats_decode_failed"); }
    }
}
