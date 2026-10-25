import * as THREE from 'three'

export function makeMapTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 1024
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')

  ctx.fillStyle = '#ead9c2'
  ctx.fillRect(0, 0, 1024, 1024)

  ctx.fillStyle = '#e3d0b4'
  const blocks: [number, number, number, number][] = [
    [70, 80, 200, 140],
    [300, 70, 160, 180],
    [500, 110, 220, 120],
    [760, 90, 170, 200],
    [80, 280, 150, 160],
    [270, 310, 240, 150],
    [560, 300, 180, 190],
    [790, 340, 150, 140],
    [90, 520, 210, 150],
    [340, 540, 170, 180],
    [560, 560, 250, 130],
    [80, 740, 180, 160],
    [300, 770, 220, 140],
    [560, 750, 160, 170],
    [760, 720, 180, 180],
  ]
  for (const [x, y, w, h] of blocks) {
    ctx.beginPath()
    ctx.roundRect(x, y, w, h, 18)
    ctx.fill()
  }

  ctx.strokeStyle = '#f6efe4'
  ctx.lineWidth = 22
  ctx.lineCap = 'square'
  ctx.beginPath()
  ctx.moveTo(0, 250)
  ctx.lineTo(1024, 250)
  ctx.moveTo(0, 690)
  ctx.lineTo(1024, 690)
  ctx.moveTo(250, 0)
  ctx.lineTo(250, 1024)
  ctx.moveTo(740, 0)
  ctx.lineTo(740, 1024)
  ctx.stroke()

  ctx.fillStyle = '#d7dcc8'
  ctx.beginPath()
  ctx.ellipse(430, 450, 70, 48, 0.4, 0, Math.PI * 2)
  ctx.fill()

  ctx.fillStyle = 'rgba(255, 250, 244, 0.45)'
  ctx.fillRect(250, 250, 490, 440)

  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  return tex
}

const badgeCache = new Map<string, THREE.CanvasTexture>()
const labelCache = new Map<string, THREE.CanvasTexture>()

export function personTexture(color: string): THREE.CanvasTexture {
  const hit = badgeCache.get(color)
  if (hit) return hit

  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 256
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')

  ctx.clearRect(0, 0, 256, 256)
  ctx.fillStyle = 'rgba(90, 64, 40, 0.18)'
  ctx.beginPath()
  ctx.ellipse(128, 214, 62, 16, 0, 0, Math.PI * 2)
  ctx.fill()

  ctx.beginPath()
  ctx.arc(128, 112, 96, 0, Math.PI * 2)
  ctx.fillStyle = color
  ctx.fill()
  ctx.lineWidth = 10
  ctx.strokeStyle = '#fffaf4'
  ctx.stroke()

  ctx.fillStyle = '#fffaf4'
  ctx.beginPath()
  ctx.arc(128, 92, 28, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.arc(128, 168, 48, Math.PI, 0)
  ctx.fill()

  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  badgeCache.set(color, tex)
  return tex
}

export function labelTexture(text: string, ink: string): THREE.CanvasTexture {
  const key = `${text}:${ink}`
  const hit = labelCache.get(key)
  if (hit) return hit

  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 128
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')
  ctx.clearRect(0, 0, 512, 128)

  ctx.fillStyle = 'rgba(255, 250, 244, 0.94)'
  ctx.beginPath()
  ctx.roundRect(16, 24, 480, 80, 40)
  ctx.fill()

  ctx.font = '600 46px Outfit, "Segoe UI", sans-serif'
  ctx.fillStyle = ink
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, 256, 66)

  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.needsUpdate = true
  labelCache.set(key, tex)
  return tex
}
