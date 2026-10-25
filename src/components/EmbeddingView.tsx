import { useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { AXES, TOKENS, axisById, type CallToken } from '../data/call.ts'
import { makeMapTexture, personTexture } from '../lib/draw.ts'

type Props = {
  revealedLines: number
  selectedId: string | null
  onSelect: (id: string | null) => void
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

function YouPin() {
  const tex = useMemo(() => personTexture('#1a73e8'), [])
  const ring = useRef<THREE.Mesh>(null)
  useFrame(() => {
    if (!ring.current) return
    const s = 1 + Math.sin(performance.now() / 420) * 0.05
    ring.current.scale.set(s, s, 1)
  })
  return (
    <group position={[0, 2.2, 0]}>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, -1.5, 0]}>
        <ringGeometry args={[5.4, 6.1, 48]} />
        <meshBasicMaterial color="#1a73e8" transparent opacity={0.85} side={THREE.DoubleSide} />
      </mesh>
      <sprite scale={[9, 9, 1]} position={[0, 1.2, 0]}>
        <spriteMaterial map={tex} transparent depthWrite={false} />
      </sprite>
      <Html center position={[0, -1.2, 5]} zIndexRange={[12, 0]} style={{ pointerEvents: 'none' }}>
        <div className="you-label">YOU</div>
      </Html>
    </group>
  )
}

function Districts({ revealedLines, onSelect }: { revealedLines: number; onSelect: (id: string) => void }) {
  return (
    <>
      {AXES.map((axis) => {
        const token = TOKENS.find((item) => item.axis === axis.id)
        return (
          <group key={axis.id} position={axis.position}>
            <mesh
              rotation={[-Math.PI / 2, 0, 0]}
              position={[0, 0.05, 0]}
              onClick={(event) => {
                event.stopPropagation()
                if (token && token.line < revealedLines) onSelect(token.id)
              }}
            >
              <circleGeometry args={[5.4, 48]} />
              <meshBasicMaterial color={axis.disc} />
            </mesh>
          </group>
        )
      })}
    </>
  )
}

function FlyingPhrase({
  token,
  settled,
  selected,
  onSelect,
}: {
  token: CallToken
  settled: boolean
  selected: boolean
  onSelect: (id: string) => void
}) {
  const group = useRef<THREE.Group>(null)
  const shaft = useRef<THREE.Mesh>(null)
  const head = useRef<THREE.Mesh>(null)
  const progress = useRef(settled ? 1 : 0)
  const axis = axisById(token.axis)
  const labelOffset = useMemo(() => {
    const len = Math.hypot(axis.position[0], axis.position[2]) || 1
    return [(axis.position[0] / len) * 3.2, 2.2, (axis.position[2] / len) * 3.2] as const
  }, [axis.position])
  const start = useMemo(() => new THREE.Vector3(0, 2.6, 0), [])
  const end = useMemo(
    () => new THREE.Vector3(axis.position[0], 2.6, axis.position[2]),
    [axis.position],
  )
  const up = useMemo(() => new THREE.Vector3(0, 1, 0), [])
  const dir = useMemo(() => new THREE.Vector3(), [])

  useFrame((_, dt) => {
    if (settled) progress.current = 1
    else progress.current = Math.min(1, progress.current + dt / 1.15)

    const t = progress.current
    const eased = t * t * (3 - 2 * t)
    const tokenGroup = group.current
    const shaftMesh = shaft.current
    const headMesh = head.current
    if (!tokenGroup || !shaftMesh || !headMesh) return

    tokenGroup.position.lerpVectors(start, end, eased)
    if (eased > 0.995) {
      tokenGroup.position.y = end.y + Math.sin(performance.now() / 700 + end.x) * 0.12
    }

    dir.set(tokenGroup.position.x, 0, tokenGroup.position.z)
    const length = dir.length()
    if (length < 8) {
      shaftMesh.visible = false
      headMesh.visible = false
      return
    }
    shaftMesh.visible = true
    headMesh.visible = true
    dir.multiplyScalar(1 / length)
    const startDist = 6.5
    const endDist = Math.max(startDist + 0.2, length - 1.8)
    const shaftLen = endDist - startDist
    shaftMesh.position.set(dir.x * (startDist + shaftLen / 2), 2.2, dir.z * (startDist + shaftLen / 2))
    shaftMesh.scale.set(1, shaftLen, 1)
    shaftMesh.quaternion.setFromUnitVectors(up, dir)
    headMesh.position.set(dir.x * endDist, 2.2, dir.z * endDist)
    headMesh.quaternion.copy(shaftMesh.quaternion)
    const material = shaftMesh.material as THREE.MeshBasicMaterial
    material.opacity = selected ? 0.95 : 0.72
  })

  return (
    <>
      <mesh ref={shaft}>
        <cylinderGeometry args={[0.16, 0.16, 1, 8]} />
        <meshBasicMaterial color={selected ? '#9a3412' : '#b4633a'} transparent opacity={0.75} />
      </mesh>
      <mesh ref={head}>
        <coneGeometry args={[0.62, 1.8, 8]} />
        <meshBasicMaterial color={selected ? '#9a3412' : '#b4633a'} />
      </mesh>
      <group ref={group}>
        <mesh
          scale={selected ? 1.35 : 1}
          onClick={(event) => {
            event.stopPropagation()
            onSelect(token.id)
          }}
          onPointerOver={() => {
            document.body.style.cursor = 'pointer'
          }}
          onPointerOut={() => {
            document.body.style.cursor = ''
          }}
        >
          <sphereGeometry args={[1.25, 24, 24]} />
          <meshBasicMaterial color={selected ? '#9a3412' : axis.ink} />
        </mesh>
        <Html center position={labelOffset} zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className={selected ? 'phrase-label on' : 'phrase-label'}>
            <small>{axis.label}</small>
            {token.quote}
          </div>
        </Html>
      </group>
    </>
  )
}

function Scene({ revealedLines, selectedId, onSelect }: Props) {
  const visible = TOKENS.filter((token) => token.line < revealedLines)
  return (
    <>
      <color attach="background" args={['#efe2cf']} />
      <MapGround />
      <YouPin />
      <Districts revealedLines={revealedLines} onSelect={onSelect} />
      {visible.map((token) => (
        <FlyingPhrase
          key={token.id}
          token={token}
          settled={token.line < revealedLines - 1}
          selected={token.id === selectedId}
          onSelect={onSelect}
        />
      ))}
      <OrbitControls
        enablePan={false}
        minDistance={28}
        maxDistance={120}
        maxPolarAngle={1.2}
        minPolarAngle={0.3}
        target={[0, 0, 0]}
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
        camera={{ position: [0, 34, 64], fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
        onPointerMissed={() => props.onSelect(null)}
      >
        <Scene {...props} />
      </Canvas>
    </div>
  )
}
