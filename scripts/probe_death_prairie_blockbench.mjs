// Optional, networked oracle generation; not part of CI or the production export.
// Runs upstream 5.2.1 interpolation/display/stack methods against the real source.
// Usage: node scripts/probe_death_prairie_blockbench.mjs > <temporary JSON>
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';

const base = 'https://raw.githubusercontent.com/JannisX11/blockbench/v5.2.1/';
const urls = [base + 'js/animations/timeline_animators.js',
  base + 'js/animations/animation_mode.js',
  'https://raw.githubusercontent.com/mrdoob/three.js/r129/build/three.js'];
const code = await Promise.all(urls.map(async url => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status}: ${url}`);
  return response.text();
}));
const context = vm.createContext({console});
vm.runInContext(code[2], context);
const THREE = context.THREE;
const raw = fs.readFileSync(new URL('../minecraft/source_assets/death_prairie_dog/deathprairie.bbmodel', import.meta.url));
const source = JSON.parse(raw);
const scene = new THREE.Object3D();
const groups = source.groups.map(group => ({...group, mesh: new THREE.Object3D()}));
const byId = Object.fromEntries(groups.map(g => [g.uuid, g]));
for (const group of groups) {
  group.constructor = {animator: true};
  group.mesh.rotation.order = 'ZYX';
}
function parentNodes(nodes, parent) {
  for (const node of nodes) {
    const group = byId[node.uuid];
    (parent?.mesh ?? scene).add(group.mesh);
    group.mesh.position.fromArray(group.origin);
    if (parent) group.mesh.position.sub(new THREE.Vector3().fromArray(parent.origin));
    group.mesh.fix_position = group.mesh.position.clone();
    group.mesh.fix_rotation = group.mesh.rotation.clone();
    parentNodes(node.children.filter(x => typeof x === 'object'), group);
  }
}
parentNodes(source.outliner);
// Only numeric, linear, one-data-point keys occur in the SHA-pinned source.
class Keyframe {
  static interpolation = {linear: 'linear', step: 'step', catmullrom: 'catmullrom', bezier: 'bezier'};
  constructor(key) {
    Object.assign(this, key);
    if (key.interpolation !== 'linear' || key.data_points.length !== 1) throw new Error('unsupported oracle key');
  }
  calc(axis) { return Number(this.data_points[0][axis]); }
  getLerp(other, axis, alpha) { return this.calc(axis)*(1-alpha) + other.calc(axis)*alpha; }
}
const Animator = {_last_values: {}, MolangParser: {context: {}},
  resetLastValues() { this._last_values = {}; }, resetParticles() {}};
Object.assign(context, {Keyframe, Animator, Group: {all: groups}, Outliner: {elements: []},
  Animation: {}, NullObject: {all: []}, Canvas: {scene},
  Format: {euler_order: 'ZYX'}, Reusable: {quat1: new THREE.Quaternion()},
  Blockbench: {dispatchEvent() {}, hasFlag() {return false;}}});
vm.runInContext('Math.degToRad = x => x*Math.PI/180; Math.radToDeg = x => x*180/Math.PI; Math.epsilon = (a,b,e) => Math.abs(a-b)<e; Math.getLerp = (a,b,t) => (t-a)/(b-a);', context);
const start = code[0].indexOf('\tdisplayRotation(arr');
const end = code[0].indexOf('\tapplyAnimationPreset(', start);
const methods = vm.runInContext('(class {' + code[0].slice(start, end) + '})', context).prototype;
const stackStart = code[1].indexOf('\tstackAnimations(animations');
const stackEnd = code[1].indexOf('\tpreview(in_loop)', stackStart);
Animator.stackAnimations = vm.runInContext('({' + code[1].slice(stackStart, stackEnd) + '}).stackAnimations', context);
const frames = [];
for (const original of source.animations) {
  const animation = {...original, animators: {}};
  animation.getBoneAnimator = group => animation.animators[group.uuid];
  for (const group of groups) {
    const src = original.animators[group.uuid] ?? {};
    const animator = Object.create(methods);
    Object.assign(animator, {group, animation, rotation_global: src.rotation_global,
      muted: {}, channels: {}, getGroup() {}, doRender() {return true;}});
    for (const channel of ['rotation', 'position', 'scale'])
      animator[channel] = (src.keyframes ?? []).filter(k => k.channel === channel).map(k => new Keyframe(k));
    animation.animators[group.uuid] = animator;
  }
  for (const time of [0, original.length/4, original.length/2, original.length*3/4, original.length]) {
    for (const group of groups) {
      group.mesh.rotation.copy(group.mesh.fix_rotation);
      group.mesh.position.copy(group.mesh.fix_position);
      group.mesh.scale.set(1,1,1);
    }
    animation.time = time;
    context.Timeline = {time};
    Animator.stackAnimations([animation], false);
    frames.push({animation: original.name, time, bones: Object.fromEntries(groups.map(g => [g.name, {
      quaternion: g.mesh.getWorldQuaternion(new THREE.Quaternion()).toArray(),
      position: g.mesh.getWorldPosition(new THREE.Vector3()).toArray(),
    }]))});
  }
}
console.log(JSON.stringify({source_sha256: crypto.createHash('sha256').update(raw).digest('hex'),
  upstream: urls.map((url,i) => ({url, sha256: crypto.createHash('sha256').update(code[i]).digest('hex')})),
  euler_order: 'ZYX', group_order: groups.map(g => g.name), frames}, null, 2));
