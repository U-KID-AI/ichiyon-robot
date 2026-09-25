"""Independent matrix oracle, source preservation, isolated A/B and switch checks.

The stored oracle was evaluated by upstream Blockbench + THREE, not the exporter.
The matrix evaluator below deliberately does not use the exporter's quaternions.
Bedrock channels are decoded to Blockbench coordinates with the official import
signs; ZYX means applying X, then Y, then Z to vectors.
"""
import hashlib
import json
import math
from pathlib import Path
import unittest

from export_death_prairie_dog import ANIMATION_IDS, ROOT, RP, SOURCE, SOURCE_SHA, quat_from_zyx, quat_to_zyx

I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def mul(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mv(a, v):
    return [sum(a[i][k]*v[k] for k in range(3)) for i in range(3)]


def transpose(a):
    return list(map(list, zip(*a)))


def matrix(v):
    x, y, z = map(math.radians, v)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return mul(mul([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]],
                   [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]),
               [[1, 0, 0], [0, cx, -sx], [0, sx, cx]])


def quaternion_matrix(q):
    x, y, z, w = q
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def sample(track, time):
    if isinstance(track, list):
        return track
    if not track:
        return [0, 0, 0]
    keys = sorted((float(t), v) for t, v in track.items())
    a = next((k for k in reversed(keys) if k[0] <= time), keys[0])
    b = next((k for k in keys if k[0] >= time), keys[-1])
    alpha = (time-a[0])/(b[0]-a[0]) if a != b else 0
    return [x*(1-alpha)+y*alpha for x, y in zip(a[1], b[1])]


def hierarchy(source):
    groups = {g['uuid']: g for g in source['groups']}
    parents = {}
    def visit(node, parent=None):
        name = groups[node['uuid']]['name']
        parents[name] = parent
        for child in node['children']:
            if isinstance(child, dict):
                visit(child, name)
    for node in source['outliner']:
        visit(node)
    return parents


def source_tracks(animation):
    tracks = {}
    for animator in animation['animators'].values():
        if animator['type'] != 'bone':
            continue
        track = tracks[animator['name']] = {}
        for key in animator.get('keyframes', []):
            track.setdefault(key['channel'], {})[str(key['time'])] = [float(key['data_points'][0][a]) for a in 'xyz']
        if animator.get('rotation_global'):
            track['global'] = True
    return tracks


def decode_bedrock(bones, time):
    result = {}
    for name, track in bones.items():
        x, y, z = sample(track.get('rotation'), time)
        px, py, pz = sample(track.get('position'), time)
        result[name] = {'rotation': [-x, -y, z], 'position': [-px, py, pz]}
        if 'relative_to' in track:
            result[name]['global'] = True
    return result


def world_pose(source, tracks, time=0, mode='local'):
    parents = hierarchy(source)
    origins = {g['name']: g['origin'] for g in source['groups']}
    local = {name: I for name in parents}
    positions = {name: [v-(origins[parents[name]][i] if parents[name] else 0)
                        for i, v in enumerate(origins[name])] for name in parents}
    def rotation(name):
        return mul(rotation(parents[name]), local[name]) if name else I
    # Actual saved group order for preview. Parent-first for relative_to semantics.
    order = [g['name'] for g in source['groups']] if mode == 'preview' else list(parents)
    for name in order:
        track = tracks.get(name, {})
        local[name] = matrix(sample(track.get('rotation'), time))
        if track.get('global'):
            local[name] = mul(transpose(rotation(parents[name])), local[name])
        positions[name] = [a+b for a, b in zip(positions[name], sample(track.get('position'), time))]
    def position(name):
        parent = parents[name]
        if not parent:
            return positions[name]
        return [a+b for a, b in zip(position(parent), mv(rotation(parent), positions[name]))]
    return {name: {'rotation': rotation(name), 'position': position(name)} for name in parents}


class DeathPrairiePoseChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads(SOURCE.read_bytes())
        cls.animations = json.loads((RP/'animations/death_prairie_dog.animation.json').read_bytes())['animations']
        cls.original = {a['name']: a for a in cls.source['animations']}
        cls.oracle = json.loads(Path(__file__).with_name('death_prairie_blockbench_pose_oracle.json').read_bytes())

    def assertVector(self, a, b):
        for x, y in zip(a, b, strict=True):
            self.assertAlmostEqual(x, y, places=8)

    def assertPose(self, a, b):
        self.assertEqual(set(a), set(b))
        for name in a:
            with self.subTest(bone=name):
                self.assertVector(a[name]['position'], b[name]['position'])
                for x, y in zip(a[name]['rotation'], b[name]['rotation']):
                    self.assertVector(x, y)

    def baked(self, name, time):
        return decode_bedrock(self.animations[ANIMATION_IDS[name]]['bones'], time)

    def test_official_executed_oracle_and_dense_world_transform_equivalence(self):
        self.assertEqual(self.oracle['source_sha256'], SOURCE_SHA)
        self.assertEqual(self.oracle['group_order'], [g['name'] for g in self.source['groups']])
        for frame in self.oracle['frames']:
            expected = {n: {'rotation': quaternion_matrix(v['quaternion']), 'position': v['position']}
                        for n, v in frame['bones'].items()}
            self.assertPose(world_pose(self.source, self.baked(frame['animation'], frame['time'])), expected)
        # Check interpolation, not just keyframes. This also checks every pivot
        # and descendant, so all unchanged cube vertices inherit equal transforms.
        for name, original in self.original.items():
            for index in range(101):
                time = original['length']*index/100
                self.assertPose(world_pose(self.source, self.baked(name, time)),
                                world_pose(self.source, source_tracks(original), time, 'preview'))

    def test_zyx_quaternion_roundtrip_including_mixed_axes_and_gimbal_lock(self):
        for angles in ([35, -42, 63], [-110, 22, 170], [32, 90, 15], [0, -90, 90], [90, 0, 0]):
            actual = matrix(quat_to_zyx(quat_from_zyx(angles)))
            for a, b in zip(actual, matrix(angles)):
                self.assertVector(a, b)

    def test_isolated_a_relative_to_versus_b_local(self):
        for name in ('animation.model.new', 'prairie4walk'):
            time = .5 if name == 'animation.model.new' else 0
            original = self.original[name]
            expected = world_pose(self.source, source_tracks(original), time, 'preview')
            # Reconstruct A only in this test: the previous official-compatible
            # relative_to/entity export, including the global-zero workaround.
            a = source_tracks(original)
            for track in a.values():
                if track.get('global'):
                    track.setdefault('rotation', [0, 0, .01])
                    if isinstance(track['rotation'], dict):
                        track['rotation'] = {t: ([0, 0, .01] if v == [0, 0, 0] else v)
                                             for t, v in track['rotation'].items()}
            old = world_pose(self.source, a, time, 'entity')
            for bone in ('head', 'tail'):
                # A is 90 degrees away; B matches the actual preview.
                delta = mul(transpose(expected[bone]['rotation']), old[bone]['rotation'])
                angle = math.degrees(math.acos(max(-1, min(1, (sum(delta[i][i] for i in range(3))-1)/2))))
                self.assertAlmostEqual(angle, 90, places=5)
            self.assertPose(world_pose(self.source, self.baked(name, time)), expected)
        for animation in self.animations.values():
            self.assertNotIn('relative_to', json.dumps(animation))
            self.assertNotIn('0.01', json.dumps(animation))

    def test_transition_boundary_rotations_and_blended_full_transform(self):
        end = self.baked('animation.model.new', .5)
        start = self.baked('prairie4walk', 0)
        end_pose, start_pose = world_pose(self.source, end), world_pose(self.source, start)
        for name in end_pose:
            for a, b in zip(end_pose[name]['rotation'], start_pose[name]['rotation']):
                self.assertVector(a, b)
        for name in ('head', 'tail', 'body', 'bone'):
            self.assertPose({name: end_pose[name]}, {name: start_pose[name]})
        # Source walking feet/arms start one model unit from the static pose.
        # Do not falsify raw full-transform equality or alter these source keys.
        for name in ('regL', 'regR', 'armL', 'armR'):
            self.assertAlmostEqual(math.dist(end_pose[name]['position'], start_pose[name]['position']), 1)
        states = json.loads((RP/'animation_controllers/death_prairie_dog.controller.json').read_bytes())['animation_controllers']['controller.animation.death_prairie_dog.state']['states']
        for state in states.values():
            self.assertEqual(state['blend_transition'], .1)
        # The departing transition pose contributes 100% at cross-fade start.
        for alpha in (0, .25, .5, .75, 1):
            blended = {}
            for name in hierarchy(self.source):
                blended[name] = {c: [x*(1-alpha)+y*alpha for x, y in zip(
                    sample(end.get(name, {}).get(c), 0), sample(start.get(name, {}).get(c), 0))]
                    for c in ('rotation', 'position')}
            if alpha == 0:
                self.assertPose(world_pose(self.source, blended), end_pose)
            if alpha == 1:
                self.assertPose(world_pose(self.source, blended), start_pose)

    def test_normal_return_has_no_residual_rotation(self):
        for time in (0, .25, .5, .75, 1):
            normal = world_pose(self.source, self.baked('prairie2walk', time))
            self.assertPose(normal, world_pose(self.source, source_tracks(self.original['prairie2walk']), time, 'preview'))
            for bone in normal.values():
                for a, b in zip(bone['rotation'], I):
                    self.assertVector(a, b)
        # Only one controller is played; no transition/chase clip outside it can
        # continue contributing after its outgoing .1s blend reaches zero.
        client = json.loads((RP/'entity/death_prairie_dog.entity.json').read_bytes())['minecraft:client_entity']['description']
        self.assertEqual(client['scripts']['animate'], ['state'])

    def test_preserved_geometry_texture_and_source(self):
        for path, sha in ((SOURCE, SOURCE_SHA),
                          (RP/'models/entity/death_prairie_dog.geo.json', '386738a98e458205242d1757c60128383f7e4e164a838441f881cf176e38fe1f'),
                          (RP/'textures/entity/death_prairie_dog.png', 'b3da75b19dd10d0e010dd5bd0157d93f70bd83a4ceb6f9535472a8921df27498')):
            data = path.read_bytes()
            # Git checkouts may use CRLF; canonical Git/installed JSON is LF.
            if path.suffix == '.json':
                data = data.replace(b'\r\n', b'\n')
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)


if __name__ == '__main__':
    unittest.main()
