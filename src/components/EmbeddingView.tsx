import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import type { ApiStudent, Pair, Xyz } from '../lib/api.ts'
import { makeMapTexture, personTexture } from '../lib/draw.ts'

type Props = {
  students: ApiStudent[]
  caller: ApiStudent | null
  nearIds: string[]
  pairs: Pair[]
  themeColor: (theme: string | null) => string
  selectedId: string | null
  onSelect: (id: string | null) => void
}

/** xyz from the API is roughly -1.2 to 1.2 on each axis. Spread it over the map, y as height. */
const SPREAD = 44
function toWorld(xyz: Xyz): THREE.Vector3 {
  return new THREE.Vector3(xyz[0] * SPREAD, 3 + (xyz[1] + 1.2) * 9, xyz[2] * SPREAD)
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
function Dots({ students, nearIds, themeColor, selectedId, onSelect }: Omit<Props, 'caller' | 'pairs'>) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const placed = useMemo(() => students.filter((row) => row.xyz), [students])
  const near = useMemo(() => new Set(nearIds), [nearIds])

  useLayoutEffect(() => {
    const target = mesh.current
    if (!target) return
    const m = new THREE.Matrix4()
    const color = new THREE.Color()
    placed.forEach((row, i) => {
      const at = toWorld(row.xyz!)
      const s = row.student_id === selectedId ? 2.1 : near.has(row.student_id) ? 1.6 : row.is_synthetic ? 0.75 : 1.3
      m.makeScale(s, s, s).setPosition(at)
      target.setMatrixAt(i, m)
      target.setColorAt(i, color.set(near.has(row.student_id) || row.student_id === selectedId ? '#9a3412' : themeColor(row.theme)))
    })
    target.count = placed.length
    target.instanceMatrix.needsUpdate = true
    if (target.instanceColor) target.instanceColor.needsUpdate = true
  }, [placed, near, themeColor, selectedId])

  const selected = placed.find((row) => row.student_id === selectedId)

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
      {selected && (
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

function Segments({ points, color, opacity }: { points: THREE.Vector3[]; color: string; opacity: number }) {
  const geometry = useMemo(() => new THREE.BufferGeometry().setFromPoints(points), [points])
  useEffect(() => () => geometry.dispose(), [geometry])
  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color={color} transparent opacity={opacity} />
    </lineSegments>
  )
}

/** Faint lines between every pair matching has connected (GET /pairs). */
function PairLines({ students, pairs }: { students: ApiStudent[]; pairs: Pair[] }) {
  const points = useMemo(() => {
    const at = new Map(students.filter((row) => row.xyz).map((row) => [row.student_id, toWorld(row.xyz!)]))
    const out: THREE.Vector3[] = []
    for (const pair of pairs) {
      const a = at.get(pair.a)
      const b = at.get(pair.b)
      if (a && b) out.push(a, b)
    }
    return out
  }, [students, pairs])
  return points.length > 0 ? <Segments points={points} color="#6f6256" opacity={0.35} /> : null
}

/** The caller's pin. It flies from the center to where their embedding lands, then lines reach its nearest students. */
function Caller({ caller, students, nearIds }: { caller: ApiStudent; students: ApiStudent[]; nearIds: string[] }) {
  const tex = useMemo(() => personTexture('#1a73e8'), [])
  useEffect(() => () => tex.dispose(), [tex])
  const group = useRef<THREE.Group>(null)
  const ring = useRef<THREE.Mesh>(null)
  const target = useMemo(() => (caller.xyz ? toWorld(caller.xyz) : new THREE.Vector3(0, 3, 0)), [caller.xyz])
  const from = useRef(new THREE.Vector3(0, 3, 0))
  const progress = useRef(0)

  useEffect(() => {
    if (group.current) from.current.copy(group.current.position)
    progress.current = 0
  }, [target])

  useFrame((_, dt) => {
    progress.current = Math.min(1, progress.current + dt / 1.4)
    const t = progress.current
    const eased = t * t * (3 - 2 * t)
    group.current?.position.lerpVectors(from.current, target, eased)
    if (ring.current) {
      const s = 1 + Math.sin(performance.now() / 420) * 0.05
      ring.current.scale.set(s, s, 1)
    }
  })

  const lines = useMemo(() => {
    const at = new Map(students.filter((row) => row.xyz).map((row) => [row.student_id, toWorld(row.xyz!)]))
    const out: THREE.Vector3[] = []
    for (const id of nearIds) {
      const end = at.get(id)
      if (end) out.push(target, end)
    }
    return out
  }, [students, nearIds, target])

  return (
    <>
      {lines.length > 0 && <Segments points={lines} color="#9a4e24" opacity={0.7} />}
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
    </>
  )
}

function Scene(props: Props) {
  const { students, caller, nearIds, pairs } = props
  return (
    <>
      <color attach="background" args={['#efe2cf']} />
      <MapGround />
      <PairLines students={students} pairs={pairs} />
      <Dots {...props} />
      {caller && <Caller caller={caller} students={students} nearIds={nearIds} />}
      <OrbitControls
        enablePan={false}
        minDistance={28}
        maxDistance={140}
        maxPolarAngle={1.25}
        minPolarAngle={0.2}
        target={[0, 6, 0]}
      />
    </>
  )
}

export function EmbeddingView(props: Props) {
  return (
    <div
      className="graph-fill"
      onMouseLeave={() => {
        document.body.style.cursor = ''
      }}
    >
      <Canvas
        camera={{ position: [0, 60, 84], fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
        onPointerMissed={() => props.onSelect(null)}
      >
        <Scene {...props} />
      </Canvas>
    </div>
  )
}
