import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import type { Student } from '../data/students.ts'
import { labelTexture, makeMapTexture, personTexture } from '../lib/draw.ts'

type GraphNode = {
  id: string
  label: string
  color: string
  x: number
  y: number
  z: number
  fx: number
  fy: number
  fz: number
}

type Props = {
  students: Student[]
  selectedId: string | null
  onSelect: (id: string) => void
}

function measure(el: HTMLElement | null) {
  if (!el) return { w: 800, h: 600 }
  const rect = el.getBoundingClientRect()
  return { w: Math.max(rect.width, 200), h: Math.max(rect.height, 200) }
}

export function PeopleGraph({ students, selectedId, onSelect }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const fgRef = useRef<any>(null)
  const selectedRef = useRef(selectedId)
  const rings = useRef<Map<string, THREE.Mesh>>(new Map())
  const [dims, setDims] = useState(() => measure(null))
  const [ready, setReady] = useState(false)

  selectedRef.current = selectedId

  useEffect(() => {
    let cancelled = false
    document.fonts.ready.then(() => {
      if (!cancelled) setReady(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const el = wrapRef.current
    const read = () => {
      const next = measure(el)
      setDims((prev) => (prev.w === next.w && prev.h === next.h ? prev : next))
    }
    read()
    const observer = new ResizeObserver(() => requestAnimationFrame(read))
    if (el) observer.observe(el)
    window.addEventListener('resize', read)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', read)
    }
  }, [])

  const graphData = useMemo(() => {
    const you: GraphNode = {
      id: 'you',
      label: 'YOU',
      color: '#1a73e8',
      x: 0,
      y: 0,
      z: 0,
      fx: 0,
      fy: 0,
      fz: 0,
    }
    const nodes: GraphNode[] = [
      you,
      ...students.map((student) => ({
        id: student.id,
        label: `${student.short} · ${student.score}`,
        color: student.color,
        x: student.x,
        y: 0,
        z: student.z,
        fx: student.x,
        fy: 0,
        fz: student.z,
      })),
    ]
    const links = students.map((student) => ({
      source: 'you',
      target: student.id,
      score: student.score,
    }))
    return { nodes, links }
  }, [students])

  const nodeThreeObject = useCallback((node: GraphNode) => {
    const isYou = node.id === 'you'
    const group = new THREE.Group()
    const size = isYou ? 13 : 9

    const badge = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: personTexture(node.color),
        transparent: true,
        depthWrite: false,
      }),
    )
    badge.scale.set(size, size, 1)
    badge.position.y = 1.2
    badge.renderOrder = 2
    group.add(badge)

    const label = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: labelTexture(node.label, isYou ? '#1a73e8' : '#2c261f'),
        transparent: true,
        depthWrite: false,
      }),
    )
    label.scale.set(isYou ? 8 : 11, isYou ? 2 : 2.8, 1)
    label.position.set(0, 0.35, isYou ? 8 : 6.2)
    label.renderOrder = 3
    group.add(label)

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(isYou ? 6.6 : 4.8, 0.22, 8, 48),
      new THREE.MeshBasicMaterial({ color: isYou ? '#1a73e8' : '#9a3412' }),
    )
    ring.rotation.x = Math.PI / 2
    ring.position.y = 0.15
    ring.visible = false
    rings.current.set(node.id, ring)
    group.add(ring)

    return group
  }, [])

  useEffect(() => {
    if (!ready) return
    const fg = fgRef.current
    if (!fg) return

    const scene: THREE.Scene = fg.scene()
    scene.fog = null
    scene.background = new THREE.Color('#efe2cf')

    const renderer: THREE.WebGLRenderer = fg.renderer()
    renderer.toneMapping = THREE.NoToneMapping
    renderer.outputColorSpace = THREE.SRGBColorSpace

    const camera = fg.camera() as THREE.PerspectiveCamera
    camera.fov = 46
    camera.updateProjectionMatrix()
    fg.cameraPosition({ x: 0, y: 78, z: 68 }, { x: 0, y: 0, z: 0 }, 0)
    const controls = fg.controls()
    controls.minPolarAngle = 0.28
    controls.maxPolarAngle = 1.2
    controls.enablePan = true

    const groundTex = makeMapTexture()
    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(68, 80),
      new THREE.MeshBasicMaterial({ map: groundTex }),
    )
    ground.rotation.x = -Math.PI / 2
    ground.position.y = -0.8
    scene.add(ground)

    const rim = new THREE.Mesh(
      new THREE.RingGeometry(66.5, 68, 80),
      new THREE.MeshBasicMaterial({ color: '#dcc9ae', side: THREE.DoubleSide }),
    )
    rim.rotation.x = -Math.PI / 2
    rim.position.y = -0.75
    scene.add(rim)

    const flow = new THREE.Group()
    const cones: { mesh: THREE.Mesh; dir: THREE.Vector3; len: number; offset: number; id: string; opacity: number }[] = []
    for (const student of students) {
      const end = new THREE.Vector3(student.x, 0, student.z)
      const len = end.length()
      const dir = end.clone().normalize()
      const thickness = 0.12 + (student.score / 100) * 0.38
      const shaft = new THREE.Mesh(
        new THREE.CylinderGeometry(thickness, thickness, 1, 8),
        new THREE.MeshBasicMaterial({
          color: '#9a4e24',
          transparent: true,
          opacity: 0.28 + (student.score / 100) * 0.45,
        }),
      )
      const shaftLen = Math.max(len - 16, 4)
      shaft.scale.y = shaftLen
      shaft.position.copy(dir).multiplyScalar(8 + shaftLen / 2)
      shaft.position.y = 0.35
      shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir)
      flow.add(shaft)

      const count = student.score >= 80 ? 4 : student.score >= 55 ? 3 : 2
      const scale = 0.85 + (student.score / 100) * 0.7
      const opacity = 0.55 + (student.score / 100) * 0.45
      for (let i = 0; i < count; i++) {
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry(1.15, 3.4, 8),
          new THREE.MeshBasicMaterial({
            color: '#7c3412',
            transparent: true,
            opacity,
            depthWrite: false,
          }),
        )
        cone.scale.set(scale, scale, scale)
        cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir)
        cone.renderOrder = 2
        flow.add(cone)
        cones.push({ mesh: cone, dir, len, offset: i / count, id: student.id, opacity })
      }
    }
    scene.add(flow)

    let frame = 0
    const tick = () => {
      const travel = (performance.now() / 2400) % 1
      const selected = selectedRef.current
      for (const cone of cones) {
        const u = (cone.offset + travel) % 1
        const dist = 8 + u * Math.max(cone.len - 14, 4)
        cone.mesh.position.copy(cone.dir).multiplyScalar(dist)
        cone.mesh.position.y = 0.7
        const material = cone.mesh.material as THREE.MeshBasicMaterial
        const dim = selected && selected !== 'you' && selected !== cone.id ? 0.18 : 1
        material.opacity = cone.opacity * dim
      }
      rings.current.forEach((ring, id) => {
        ring.visible = id === selected
      })
      frame = requestAnimationFrame(tick)
    }
    tick()

    return () => {
      cancelAnimationFrame(frame)
      scene.remove(ground)
      scene.remove(rim)
      scene.remove(flow)
      ground.geometry.dispose()
      ;(ground.material as THREE.Material).dispose()
      groundTex.dispose()
      rim.geometry.dispose()
      ;(rim.material as THREE.Material).dispose()
      flow.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose()
          const material = obj.material
          if (Array.isArray(material)) material.forEach((item) => item.dispose())
          else material.dispose()
        }
      })
    }
  }, [ready, students])

  return (
    <div
      ref={wrapRef}
      className="graph-fill"
      onMouseLeave={() => {
        document.body.style.cursor = ''
      }}
    >
      {ready && (
      <ForceGraph3D
        ref={fgRef}
        graphData={graphData}
        width={dims.w}
        height={dims.h}
        backgroundColor="#efe2cf"
        showNavInfo={false}
        controlType="orbit"
        nodeId="id"
        nodeLabel={() => ''}
        nodeRelSize={1}
        nodeThreeObject={nodeThreeObject}
        nodeThreeObjectExtend={false}
        onNodeClick={(node: { id?: string }) => {
          if (node.id) onSelect(node.id)
        }}
        onNodeHover={(node: { id?: string } | null) => {
          document.body.style.cursor = node ? 'pointer' : ''
        }}
        onBackgroundClick={() => onSelect('')}
        linkColor={(link: { score?: number }) => {
          const score = link.score ?? 0
          const alpha = (0.22 + (score / 100) * 0.55).toFixed(2)
          return `rgba(138, 78, 36, ${alpha})`
        }}
        linkWidth={0}
        linkOpacity={1}
        linkDirectionalArrowLength={0}
        d3AlphaDecay={1}
        d3VelocityDecay={1}
        warmupTicks={0}
        cooldownTicks={0}
        enableNodeDrag={false}
      />
      )}
    </div>
  )
}
