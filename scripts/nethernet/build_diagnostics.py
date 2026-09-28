#!/usr/bin/env python3
"""Build an opt-in diagnostic overlay for the inspected MCXboxBroadcast build 154.

Requires JDK 21. Does not modify the original jar or contact a running server.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import shutil
import tempfile
import urllib.request
import zipfile

REVISION = 'd48f7d3869a22bfb5b2ff3d1e77484d877b3b05c'
BASE = 'dev/kastle/netty/channel/nethernet/'
CLASS_HASHES = {'NetherNetServerChannel': '6c440a7694d734c597e9f52efcf95b3f4f6063014d937d96ce99f41d4f747e0c',
 'signaling/NetherNetXboxRpcSignaling': 'a4f6e7da4dd5fd54586b65a15d761c3b4b05db723b9d9693d7c4a5480ad0030b',
 'signaling/AbstractNetherNetXboxSignaling': '2869c8f0fe475a2090fb2da2756b3a69b9dbc21f7f18d294df6b791f21accbea'}
SOURCE_HASHES = {'NetherNetServerChannel': '64c1417c956eb32b41cd121e6334d23f7e3569540748efdc483419d08e1e21b1',
 'signaling/NetherNetXboxRpcSignaling': 'ed762b39f6921145223bcf28849b99f120c3f03b97a68f85f58ca71325ed9477',
 'signaling/AbstractNetherNetXboxSignaling': '89d94e1a95ec9cf97b4c2478d9040e865ac47da9c68c35bd4fd319c069de8d80'}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('upstream source does not match diagnostic patch')
    return source.replace(old, new, 1)


def instrument(name, source):
    source = '// Modified: adds Ichiyon diagnostic observations; network behavior is retained.\n' + source
    source = source.replace('\nimport ', '\nimport ichiyon.nethernet.Diagnostics;\nimport ', 1)
    if name == 'NetherNetServerChannel':
        source = replace_once(source,
            'RTCPeerConnection pc = factory.createPeerConnection(rtcConfig, observer);',
            'RTCPeerConnection pc = factory.createPeerConnection(rtcConfig, observer);\n'
            '        observer.diagnosticPc = pc;\n'
            '        Diagnostics.event(connectionId, "peer_created");')
        source = replace_once(source,
            'child.closeFuture().addListener(future -> signaling.removeSignalHandler(connectionId));',
            'child.closeFuture().addListener(future -> signaling.removeSignalHandler(connectionId));\n'
            '        ScheduledFuture<?> diagnosticTimer = eventLoop().scheduleAtFixedRate(\n'
            '            () -> Diagnostics.stats(connectionId, pc), 1, 1, TimeUnit.SECONDS);\n'
            '        child.closeFuture().addListener(future -> diagnosticTimer.cancel(false));')
        source = replace_once(source, 'if (!child.isActive()) {',
            'if (!child.isActive()) {\n'
            '                Diagnostics.event(connectionId, "handshake_timeout");\n'
            '                Diagnostics.stats(connectionId, pc);')
        source = replace_once(source, 'private NetherNetChildChannel child;',
            'private NetherNetChildChannel child;\n'
            '        private volatile RTCPeerConnection diagnosticPc;\n'
            '        @Override public void onIceConnectionChange(dev.kastle.webrtc.RTCIceConnectionState state) {\n'
            '            Diagnostics.state(connectionId, "ice_state", state);\n'
            '            if (state != dev.kastle.webrtc.RTCIceConnectionState.CLOSED) Diagnostics.stats(connectionId, diagnosticPc);\n'
            '        }\n'
            '        @Override public void onIceGatheringChange(dev.kastle.webrtc.RTCIceGatheringState state) {\n'
            '            Diagnostics.state(connectionId, "gathering_state", state);\n'
            '            Diagnostics.stats(connectionId, diagnosticPc);\n'
            '        }\n'
            '        @Override public void onIceCandidateError(dev.kastle.webrtc.RTCPeerConnectionIceErrorEvent error) {\n'
            '            Diagnostics.iceError(connectionId, error);\n'
            '        }')
        source = replace_once(source, 'public void onConnectionChange(RTCPeerConnectionState state) {',
            'public void onConnectionChange(RTCPeerConnectionState state) {\n'
            '            Diagnostics.state(connectionId, "peer_state", state);\n'
            '            if (state != RTCPeerConnectionState.CLOSED) Diagnostics.stats(connectionId, diagnosticPc);')
        source = replace_once(source, 'child.setDataChannels(reliable, unreliable);',
            'Diagnostics.event(connectionId, "data_channels_established");\n'
            '                Diagnostics.stats(connectionId, diagnosticPc);\n'
            '                child.setDataChannels(reliable, unreliable);')
        for stage in ('SetLocalDesc', 'CreateAnswer', 'SetRemoteDesc'):
            source = replace_once(source, f'log.error("{stage} failed: {{}}", error);',
                f'Diagnostics.event(connectionId, "{stage}_failed"); log.error("{stage} failed: {{}}", error);')
    elif name == 'AbstractNetherNetXboxSignaling':
        source = replace_once(source, 'protected void dispatchSignalToPipeline(String sender, String rawMsg) {',
            'protected void dispatchSignalToPipeline(String sender, String rawMsg) {\n'
            '        Diagnostics.signal("in", rawMsg);')
        source = replace_once(source, 'log.debug("Successfully parsed {} ICE servers.", result.size());',
            'Diagnostics.servers(result);\n'
            '        log.debug("Successfully parsed {} ICE servers.", result.size());')
        source = replace_once(source, 'log.error("Failed to parse TURN servers", e);',
            'Diagnostics.event(0, "turn_parse_failed"); log.error("Failed to parse TURN servers", e);')
    elif name == 'NetherNetXboxRpcSignaling':
        source = replace_once(source, 'public void sendSignal(String targetNetworkId, String data) {',
            'public void sendSignal(String targetNetworkId, String data) {\n'
            '        Diagnostics.signal("out_attempt", data);')
        source = replace_once(source, 'log.error("Failed to fetch TURN credentials", t);',
            'Diagnostics.event(0, "turn_fetch_failed"); log.error("Failed to fetch TURN credentials", t);')
        source = replace_once(source, 'log.error("Error processing signaling frame: " + text, e);',
            'Diagnostics.event(0, "signaling_frame_failed"); log.error("Error processing signaling frame", e);')
        source = replace_once(source, 'future.completeExceptionally(new RuntimeException(msg));',
            'Diagnostics.event(0, "signaling_rpc_error");\n'
            '                future.completeExceptionally(new RuntimeException(msg));')
    else:
        raise ValueError('unknown source')
    return source


def build(jar, output, source_dir=None):
    if hashlib.sha256(jar.read_bytes()).hexdigest() != 'e785d47be8a724dc0537808d29e0fbb9b0e4e22b9bf80b0b9a20f21321736586':
        raise ValueError('unsupported original jar; inspect before enabling diagnostics')
    with zipfile.ZipFile(jar) as z:
        for path, digest in CLASS_HASHES.items():
            if hashlib.sha256(z.read(BASE + path + '.class')).hexdigest() != digest:
                raise ValueError('unsupported Broadcast transport; inspect its version before building')
    with tempfile.TemporaryDirectory(prefix='nethernet-build-') as tmp:
        root = Path(tmp)
        sources = []
        for path, digest in SOURCE_HASHES.items():
            if source_dir:
                data = (source_dir / (path + '.java')).read_bytes()
            else:
                url = (f'https://raw.githubusercontent.com/Kas-tle/NetworkCompatible/{REVISION}'
                       f'/transport-nethernet/src/main/java/{BASE}{path}.java')
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('upstream source checksum mismatch')
            name = path.rsplit('/', 1)[-1]
            target = root / (name + '.java')
            target.write_text(instrument(name, data.decode()), encoding='utf-8')
            sources.append(str(target))
        classes = root / 'classes'
        classes.mkdir()
        subprocess.run(['javac', '-proc:none', '--release', '21', '-cp', str(jar), '-d', str(classes),
                        str(Path(__file__).with_name('Diagnostics.java')), *sources], check=True)
        metadata = classes / 'META-INF'
        metadata.mkdir()
        shutil.copyfile(Path(__file__).with_name('LICENSE.NetworkCompatible'), metadata / 'LICENSE.NetworkCompatible')
        (metadata / 'NOTICE.Ichiyon').write_text(
            'Diagnostic modifications to three NetworkCompatible 1.7.4 classes.\n'
            'Upstream: https://github.com/Kas-tle/NetworkCompatible/tree/' + REVISION + '\n', encoding='utf-8')
        subprocess.run(['jar', '--create', '--file', str(output), '-C', str(classes), '.'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-jar', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, help='optional offline verified upstream source directory')
    args = parser.parse_args()
    build(args.original_jar.resolve(), args.output.resolve(), args.source_dir)
