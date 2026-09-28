package ichiyon.nethernet;

import dev.kastle.webrtc.*;
import dev.kastle.netty.channel.nethernet.signaling.NetherNetSignaling.IceServerInfo;
import java.util.*;

/** Offline fixtures; never contacts Xbox, TURN or a game server. */
public class DiagnosticsCheck {
    static class Signaling extends dev.kastle.netty.channel.nethernet.signaling.NetherNetXboxRpcSignaling {
        Signaling() { super("1", "SECRET_TOKEN"); }
        void receive(String value) { dispatchSignalToPipeline("SECRET_PLAYER", value); }
    }
    static class Stat extends RTCStats {
        Stat(RTCStatsType type, String id, Map<String,Object> values) { super(1, type, id, values); }
    }
    static class Report extends RTCStatsReport {
        Report(Map<String,RTCStats> values) { super(values, 1); }
    }
    public static void main(String[] args) throws Exception {
        io.netty.util.internal.logging.InternalLoggerFactory.setDefaultFactory(io.netty.util.internal.logging.JdkLoggerFactory.INSTANCE);
        Signaling signaling = new Signaling();
        int[] received = {0};
        signaling.setNewConnectionHandler((id, sender, payload) -> received[0]++);
        signaling.receive("CONNECTREQUEST 18446744073709551615 v=0\r\na=identity:SECRET_IDENTITY\r\na=ice-pwd:SECRET_ICE\r\na=candidate:1 1 udp 123 192.0.2.1 19140 typ host ufrag SECRET_UFRAG\r\n");
        try {
            signaling.sendSignal("SECRET_PLAYER", "CONNECTRESPONSE 18446744073709551615 SECRET_SDP");
            throw new AssertionError("inactive signaling must retain original behavior");
        } catch (IllegalStateException expected) { }
        signaling.setSignalHandler(-1, signal -> received[0]++);
        signaling.receive("CANDIDATEADD 18446744073709551615 candidate:1 1 udp 123 2001:db8::1 3210 typ relay raddr 10.0.0.1 ufrag SECRET_UFRAG");
        if (received[0] != 2) throw new AssertionError("diagnostics changed dispatch");
        Diagnostics.signal("in", "CANDIDATEADD broken SECRET_PAYLOAD");
        Diagnostics.servers(List.of(new IceServerInfo("SECRET_USER", "SECRET_PASSWORD", List.of("turn:secret.example:3478"))));
        Map<String,RTCStats> stats = new LinkedHashMap<>();
        stats.put("transport", new Stat(RTCStatsType.TRANSPORT, "transport", Map.of("selectedCandidatePairId", "pair")));
        stats.put("pair", new Stat(RTCStatsType.CANDIDATE_PAIR, "pair", Map.of("localCandidateId", "local", "remoteCandidateId", "remote", "state", "succeeded", "nominated", true, "bytesSent", 44, "SECRET_KEY", "SECRET_STATS")));
        stats.put("local", new Stat(RTCStatsType.LOCAL_CANDIDATE, "local", Map.of("candidateType", "srflx", "address", "192.0.2.1", "port", 19140, "protocol", "udp", "usernameFragment", "SECRET_UFRAG")));
        stats.put("remote", new Stat(RTCStatsType.REMOTE_CANDIDATE, "remote", Map.of("candidateType", "relay", "address", "2001:db8::1", "port", 3210, "protocol", "udp")));
        Diagnostics.report(-1, new Report(stats));
        Diagnostics.state(-1, "ice_state", RTCIceConnectionState.CONNECTED);
        Diagnostics.event(-1, "data_channels_established");
        // Ensure every patched class verifies against the exact runtime dependency set.
        for (String name : List.of("dev.kastle.netty.channel.nethernet.NetherNetServerChannel",
                "dev.kastle.netty.channel.nethernet.signaling.AbstractNetherNetXboxSignaling",
                "dev.kastle.netty.channel.nethernet.signaling.NetherNetXboxRpcSignaling")) Class.forName(name);
        if (args.length > 0 && args[0].equals("expiry")) {
            Thread.sleep(1200);
            Diagnostics.event(42, "must_not_appear_after_expiry");
        }
        signaling.close();
    }
}
