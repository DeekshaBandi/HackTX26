import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import type { ApiStudent, Pair, Xyz } from '../lib/api.ts'
import { makeMapTexture, personTexture } from '../lib/draw.ts'

type Props = {
  students: ApiStudent[]
  caller: ApiStudent | null
  nearIds: string[]
  pairs: Pair[]
  /** Students the SerpApi count reaches. Everyone else is faded. null shows everyone. */
  reachIds: Set<string> | null
  themeColor: (theme: string | null) => string
  selectedId: string | null
  onSelect: (id: string | null) => void
}

/** xyz from the API is roughly -1.2 to 1.2 on each axis. Spread it over the map, y as height. */
const SPREAD = 44
function toWorld(xyz: Xyz): THREE.Vector3 {
  return new THREE.Vector3(xyz[0] * SPREAD, 3 + (xyz[1] + 1.2) * 9, xyz[2] * SPREAD)
}

/**
 * The match sequence: the caller's pin lands, everyone else fades out (farthest first),
 * the matches move out of their tight cluster into a ring around the caller, their labels open,
 * and the view starts spinning slowly.
 */
const FLY_S = 1.4
const FADE_S = 2.6
const RING_START_S = FLY_S + FADE_S * 0.5
const RING_S = 1.6
const LABEL_S = RING_START_S + RING_S
const SPIN_SPEED = 0.6
/** The ring around the caller, in world units. Every other match sits on the inner ring so neighbors' labels don't collide. */
const RING_R = 17
const INNER = 0.45
const BACKGROUND = '#efe2cf'
const HOME_TARGET: Xyz = [0, 6, 0]
const HOME_CAMERA: Xyz = [0, 60, 84]
const HOME_DISTANCE = Math.hypot(HOME_CAMERA[1] - HOME_TARGET[1], HOME_CAMERA[2] - HOME_TARGET[2])
const HOME_PHI = Math.acos((HOME_CAMERA[1] - HOME_TARGET[1]) / HOME_DISTANCE)
/** Closer to top-down in focus, so the ring reads as a circle */
const FOCUS_PHI = 0.55
/** Where the caller's pin starts, and stays when they have no embedding yet */
const ORIGIN = new THREE.Vector3(0, 3, 0)
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * Shared by the scene's pieces and read every frame. time: seconds since focus turned on or off.
 * fade: 0 shows everyone, 1 only the matches. ring: 0 is the matches' embedding spots, 1 the ring.
 */
type Focus = { time: number; fade: number; ring: number }

/** Where one match sits in the embedding (home) and on the ring around the caller */
type Slot = { id: string; home: THREE.Vector3; ring: THREE.Vector3 }

const clamp01 = (t: number) => Math.min(1, Math.max(0, t))
const smooth = (t: number) => t * t * (3 - 2 * t)

function worldById(students: ApiStudent[]): Map<string, THREE.Vector3> {
  return new Map(students.filter((row) => row.xyz).map((row) => [row.student_id, toWorld(row.xyz!)]))
}

function slotAt(slot: Slot, ring: number, out: THREE.Vector3): THREE.Vector3 {
  return out.lerpVectors(slot.home, slot.ring, smooth(ring))
}

function MapGround() {
  const tex = useMemo(() => makeMapTexture(), [])
  useEffect(() => () => tex.dispose(), [tex])
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.8, 0]}>
      <circleGeometry args={[72, 80]} />
      <meshBasicMaterial map={tex} />
    </mesh>
  )
}

/** Every student as one dot, colored by theme. One draw call for all of them. */
function Dots({
  students,
  caller,
  nearIds,
  reachIds,
  themeColor,
  selectedId,
  onSelect,
  focus,
  focused,
  slots,
}: Omit<Props, 'pairs'> & { focus: RefObject<Focus>; focused: boolean; slots: Map<string, Slot> }) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const placed = useMemo(() => students.filter((row) => row.xyz), [students])
  const near = useMemo(() => new Set(nearIds), [nearIds])

  // 0 for the dot farthest from the caller (it goes first), 1 for the closest
  const order = useMemo(() => {
    if (!caller?.xyz) return placed.map(() => 0)
    const from = toWorld(caller.xyz)
    const dist = placed.map((row) => toWorld(row.xyz!).distanceTo(from))
    const far = Math.max(1, ...dist)
    return dist.map((d) => 1 - d / far)
  }, [placed, caller?.xyz])

  const painted = useRef({ fade: -1, ring: -1 })
  const paint = ({ fade, ring }: Focus) => {
    const target = mesh.current
    if (!target) return
    painted.current = { fade, ring }
    const m = new THREE.Matrix4()
    const at = new THREE.Vector3()
    const color = new THREE.Color()
    const background = new THREE.Color(BACKGROUND)
    placed.forEach((row, i) => {
      const slot = slots.get(row.student_id)
      if (slot) slotAt(slot, ring, at)
      else at.copy(toWorld(row.xyz!))
      const hot = near.has(row.student_id) || row.student_id === selectedId
      const gone = near.has(row.student_id) ? 0 : smooth(clamp01(fade * 1.6 - order[i] * 0.6))
      const size = row.student_id === selectedId ? 2.1 : near.has(row.student_id) ? 1.6 + fade * 0.6 : row.is_synthetic ? 0.75 : 1.3
      const s = Math.max(size * (1 - gone), 0.0001)
      m.makeScale(s, s, s).setPosition(at)
      target.setMatrixAt(i, m)
      const faded = reachIds != null && !reachIds.has(row.student_id)
      target.setColorAt(i, color.set(hot ? '#9a3412' : faded ? '#dccfbd' : themeColor(row.theme)).lerp(background, gone))
    })
    target.count = placed.length
    target.instanceMatrix.needsUpdate = true
    if (target.instanceColor) target.instanceColor.needsUpdate = true
  }

  useLayoutEffect(() => {
    paint(focus.current)
  })

  useFrame(() => {
    const f = focus.current
    if (f.fade !== painted.current.fade || f.ring !== painted.current.ring) paint(f)
  })

  const selected = placed.find((row) => row.student_id === selectedId)
  // In focus the matches already carry a label
  const showSelected = selected && !(focused && near.has(selected.student_id))

  return (
    <>
      <instancedMesh
        ref={mesh}
        args={[undefined, undefined, Math.max(placed.length, 1)]}
        onClick={(event) => {
          event.stopPropagation()
          const row = event.instanceId != null ? placed[event.instanceId] : null
          if (row) onSelect(row.student_id)
        }}
        onPointerOver={() => {
          document.body.style.cursor = 'pointer'
        }}
        onPointerOut={() => {
          document.body.style.cursor = ''
        }}
      >
        <sphereGeometry args={[0.55, 10, 10]} />
        <meshBasicMaterial />
      </instancedMesh>
      {showSelected && (
        <Html center position={toWorld(selected.xyz!).add(new THREE.Vector3(0, 2.6, 0))} zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className="phrase-label on">
            <small>{selected.theme ?? 'No theme'}</small>
            {selected.situation ?? 'No situation on the record'}
          </div>
        </Html>
      )}
    </>
  )
}

function Segments({
  points,
  color,
  opacity,
  material,
}: {
  points: THREE.Vector3[]
  color: string
  opacity: number
  material?: RefObject<THREE.LineBasicMaterial | null>
}) {
  const geometry = useMemo(() => new THREE.BufferGeometry().setFromPoints(points), [points])
  useEffect(() => () => geometry.dispose(), [geometry])
  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial ref={material} color={color} transparent opacity={opacity} />
    </lineSegments>
  )
}

/** Faint lines between every pair matching has connected (GET /pairs). They fade out with the other dots. */
function PairLines({ students, pairs, focus }: { students: ApiStudent[]; pairs: Pair[]; focus: RefObject<Focus> }) {
  const material = useRef<THREE.LineBasicMaterial>(null)
  useFrame(() => {
    if (material.current) material.current.opacity = 0.35 * (1 - focus.current.fade)
  })
  const points = useMemo(() => {
    const at = worldById(students)
    const out: THREE.Vector3[] = []
    for (const pair of pairs) {
      const a = at.get(pair.a)
      const b = at.get(pair.b)
      if (a && b) out.push(a, b)
    }
    return out
  }, [students, pairs])
  return points.length > 0 ? <Segments points={points} color="#6f6256" opacity={0.35} material={material} /> : null
}

/** Lines from the caller to each nearest student, following them out to the ring. */
function MatchLines({ from, slots, focus }: { from: THREE.Vector3; slots: Slot[]; focus: RefObject<Focus> }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(slots.length * 6), 3))
    return g
  }, [slots])
  useEffect(() => () => geometry.dispose(), [geometry])
  const drawn = useRef(-1)

  useFrame(() => {
    const ring = focus.current.ring
    if (ring === drawn.current) return
    drawn.current = ring
    const attr = geometry.getAttribute('position') as THREE.BufferAttribute
    const at = new THREE.Vector3()
    slots.forEach((slot, i) => {
      attr.setXYZ(i * 2, from.x, from.y, from.z)
      slotAt(slot, ring, at)
      attr.setXYZ(i * 2 + 1, at.x, at.y, at.z)
    })
    attr.needsUpdate = true
    geometry.computeBoundingSphere()
  })

  // New slots mean a new buffer, so draw it on the next frame
  useLayoutEffect(() => {
    drawn.current = -1
  }, [geometry])

  return slots.length > 0 ? (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color="#9a4e24" transparent opacity={0.7} />
    </lineSegments>
  ) : null
}

/** The caller's pin. It flies from the center to where their embedding lands. */
function Caller({ target }: { target: THREE.Vector3 }) {
  const tex = useMemo(() => personTexture('#1a73e8'), [])
  useEffect(() => () => tex.dispose(), [tex])
  const group = useRef<THREE.Group>(null)
  const ring = useRef<THREE.Mesh>(null)
  const from = useRef(ORIGIN.clone())
  const progress = useRef(0)

  useEffect(() => {
    if (group.current) from.current.copy(group.current.position)
    progress.current = 0
  }, [target])

  useFrame((_, dt) => {
    progress.current = Math.min(1, progress.current + dt / FLY_S)
    const t = progress.current
    const eased = t * t * (3 - 2 * t)
    group.current?.position.lerpVectors(from.current, target, eased)
    if (ring.current) {
      const s = 1 + Math.sin(performance.now() / 420) * 0.05
      ring.current.scale.set(s, s, 1)
    }
  })

  return (
    <group ref={group} position={[0, 3, 0]}>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, -1.2, 0]}>
        <ringGeometry args={[3.2, 3.7, 48]} />
        <meshBasicMaterial color="#1a73e8" transparent opacity={0.85} side={THREE.DoubleSide} />
      </mesh>
      <sprite scale={[6, 6, 1]} position={[0, 1.2, 0]}>
        <spriteMaterial map={tex} transparent depthWrite={false} />
      </sprite>
      <Html center position={[0, -1, 3.5]} zIndexRange={[12, 0]} style={{ pointerEvents: 'none' }}>
        <div className="you-label">YOU</div>
      </Html>
    </group>
  )
}

/** One open label over a match, riding along as it moves out to the ring. */
function MatchLabel({ row, slot, index, focus }: { row: ApiStudent; slot: Slot; index: number; focus: RefObject<Focus> }) {
  const group = useRef<THREE.Group>(null)
  useFrame(() => {
    if (group.current) slotAt(slot, focus.current.ring, group.current.position)
  })
  return (
    <group ref={group}>
      <Html center zIndexRange={[16, 0]} style={{ pointerEvents: 'none' }}>
        <div className="match-label" style={{ animationDelay: `${LABEL_S + index * 0.18}s` }}>
          <small>{row.theme ?? 'No theme'}</small>
          {row.situation ?? 'No situation on the record'}
        </div>
      </Html>
    </group>
  )
}

/** What each matched student said, opening one after another once they reach the ring. */
function MatchLabels({ students, slots, focus }: { students: ApiStudent[]; slots: Slot[]; focus: RefObject<Focus> }) {
  const byId = useMemo(() => new Map(students.map((row) => [row.student_id, row])), [students])
  return (
    <>
      {slots.map((slot, i) => {
        const row = byId.get(slot.id)
        return row ? <MatchLabel key={slot.id} row={row} slot={slot} index={i} focus={focus} /> : null
      })}
    </>
  )
}

/** Tilts the camera down over the ring and fits it to the view, then spins slowly around the caller. Back home when focus turns off. */
function FocusCamera({ center, focus, focused }: { center: THREE.Vector3 | null; focus: RefObject<Focus>; focused: boolean }) {
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null)
  const camera = useThree((state) => state.camera) as THREE.PerspectiveCamera
  const aspect = useThree((state) => state.size.width / Math.max(1, state.size.height))

  const goal = useMemo(() => {
    if (!focused || !center) return { center: new THREE.Vector3(...HOME_TARGET), distance: HOME_DISTANCE, phi: HOME_PHI }
    // Room for the ring plus a label's worth on every side
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))
    const halfHeight = RING_R * Math.cos(FOCUS_PHI) + 5
    const halfWidth = RING_R + 7
    const distance = Math.max(28, halfHeight / tan, halfWidth / (tan * aspect))
    return { center, distance, phi: FOCUS_PHI }
  }, [focused, center, camera.fov, aspect])

  useFrame((_, dt) => {
    const c = controls.current
    if (!c) return
    const t = focus.current.time
    // Only steer while moving in or out, so the judge can still drag and zoom after
    if (t < (focused ? LABEL_S + 1.5 : 1.8)) {
      const k = 1 - Math.exp(-dt * 2.2)
      c.target.lerp(goal.center, k)
      const view = new THREE.Spherical().setFromVector3(camera.position.clone().sub(c.target))
      view.radius = THREE.MathUtils.lerp(view.radius, goal.distance, k)
      view.phi = THREE.MathUtils.lerp(view.phi, goal.phi, k)
      camera.position.copy(c.target).add(new THREE.Vector3().setFromSpherical(view))
    }
    const spin = focused && !reducedMotion && t > LABEL_S ? SPIN_SPEED : 0
    c.autoRotateSpeed = THREE.MathUtils.lerp(c.autoRotateSpeed, spin, 1 - Math.exp(-dt * 0.8))
    c.autoRotate = c.autoRotateSpeed > 0.01
  })

  return (
    <OrbitControls
      ref={controls}
      enablePan={false}
      minDistance={28}
      maxDistance={140}
      maxPolarAngle={1.25}
      minPolarAngle={0.2}
      autoRotateSpeed={0}
      target={HOME_TARGET}
    />
  )
}

function Scene({ focused, focusKey, ...props }: Props & { focused: boolean; focusKey: string | null }) {
  const { students, caller, nearIds, pairs } = props
  const focus = useRef<Focus>({ time: 0, fade: 0, ring: 0 })
  const center = useMemo(() => (caller?.xyz ? toWorld(caller.xyz) : null), [caller?.xyz])

  // Each match's ring spot, in score order around the caller
  const slots = useMemo(() => {
    if (!center) return []
    const at = worldById(students)
    return nearIds.flatMap((id, i): Slot[] => {
      const home = at.get(id)
      if (!home) return []
      const angle = -Math.PI / 2 + (i / nearIds.length) * Math.PI * 2
      const r = RING_R * (i % 2 ? INNER : 1)
      return [{ id, home, ring: new THREE.Vector3(center.x + Math.cos(angle) * r, center.y, center.z + Math.sin(angle) * r) }]
    })
  }, [students, center, nearIds])
  const slotById = useMemo(() => new Map(slots.map((slot) => [slot.id, slot])), [slots])

  useEffect(() => {
    focus.current.time = 0
  }, [focused, focusKey])

  useFrame((_, dt) => {
    const f = focus.current
    f.time += dt
    if (focused) {
      f.fade = clamp01((f.time - FLY_S) / FADE_S)
      f.ring = clamp01((f.time - RING_START_S) / RING_S)
    } else {
      f.fade = Math.max(0, f.fade - dt / 0.8)
      f.ring = Math.max(0, f.ring - dt / 0.8)
    }
  })

  return (
    <>
      <color attach="background" args={[BACKGROUND]} />
      <MapGround />
      <PairLines students={students} pairs={pairs} focus={focus} />
      <Dots {...props} focus={focus} focused={focused} slots={slotById} />
      {caller && (
        <>
          <MatchLines from={center ?? ORIGIN} slots={slots} focus={focus} />
          <Caller target={center ?? ORIGIN} />
        </>
      )}
      {focused && <MatchLabels key={focusKey} students={students} slots={slots} focus={focus} />}
      <FocusCamera center={center} focus={focus} focused={focused} />
    </>
  )
}

export function EmbeddingView(props: Props) {
  const { caller, nearIds } = props
  // A new caller, a new landing spot, or new matches plays the sequence again
  const focusKey = caller?.xyz && nearIds.length > 0 ? `${caller.student_id}|${caller.xyz.join(',')}|${nearIds.join(',')}` : null
  const [showAll, setShowAll] = useState(false)
  useEffect(() => setShowAll(false), [focusKey])
  const focused = focusKey !== null && !showAll

  return (
    <div
      className="graph-fill"
      onMouseLeave={() => {
        document.body.style.cursor = ''
      }}
    >
      {focusKey && (
        <button type="button" className="view-toggle" onClick={() => setShowAll((all) => !all)}>
          {showAll ? 'Show matches' : 'Show everyone'}
        </button>
      )}
      <Canvas
        camera={{ position: HOME_CAMERA, fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
        onPointerMissed={() => props.onSelect(null)}
      >
        <Scene {...props} focused={focused} focusKey={focusKey} />
      </Canvas>
    </div>
  )
}
