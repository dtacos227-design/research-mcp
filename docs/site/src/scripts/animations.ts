import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Kill all ScrollTriggers before View Transitions page swap
document.addEventListener('astro:before-swap', () => {
  ScrollTrigger.getAll().forEach(t => t.kill())
})

export function initHeroAnimation() {
  if (reduced()) return

  const title = document.getElementById('hero-title')
  if (!title) return

  const words = title.innerText.split(/\s+/)
  title.innerHTML = words
    .map(w => `<span class="inline-block opacity-0 translate-y-8">${w}&nbsp;</span>`)
    .join('')

  gsap.to('#hero-title span', {
    opacity: 1,
    y: 0,
    stagger: 0.08,
    duration: 0.65,
    ease: 'power2.out',
    delay: 0.15,
  })

  gsap.from('#hero-sub', { opacity: 0, y: 20, duration: 0.6, delay: 0.9 })
  gsap.from('#hero-cta', { opacity: 0, y: 20, duration: 0.6, delay: 1.15 })
  gsap.from('#hero-badges', { opacity: 0, duration: 0.6, delay: 1.4 })
}

export function initCaraKerjaAnimation() {
  if (reduced()) return

  const path = document.getElementById('connector-path')
  if (path) {
    const totalLen = (path as SVGLineElement).getTotalLength?.() ?? 1000
    gsap.set(path, { strokeDasharray: totalLen, strokeDashoffset: totalLen })
    gsap.to(path, {
      strokeDashoffset: 0,
      ease: 'none',
      scrollTrigger: {
        trigger: '#feature-list',
        start: 'top 70%',
        end: 'bottom 30%',
        scrub: 1,
      },
    })
  }

  ScrollTrigger.batch('.feature-card', {
    onEnter: els =>
      gsap.to(els, {
        opacity: 1,
        x: 0,
        stagger: 0.12,
        duration: 0.55,
        ease: 'power2.out',
      }),
    start: 'top 88%',
  })
}

export function initMulaiAnimation() {
  if (reduced()) return

  const stepLine = document.getElementById('step-line')
  if (stepLine) {
    const len = (stepLine as SVGLineElement).getTotalLength?.() ?? 800
    gsap.set(stepLine, { strokeDasharray: len, strokeDashoffset: len })
    gsap.to(stepLine, {
      strokeDashoffset: 0,
      ease: 'none',
      scrollTrigger: {
        trigger: '#step-list',
        start: 'top 70%',
        end: 'bottom 30%',
        scrub: 1,
      },
    })
  }

  ScrollTrigger.batch('.step-block', {
    onEnter: els =>
      gsap.to(els, {
        opacity: 1,
        x: 0,
        stagger: 0.15,
        duration: 0.6,
        ease: 'power2.out',
      }),
    start: 'top 88%',
  })
}

export function initReferensiAnimation() {
  if (reduced()) return

  ScrollTrigger.batch('.ref-row', {
    onEnter: els => gsap.to(els, { opacity: 1, y: 0, stagger: 0.04, duration: 0.4 }),
    start: 'top 92%',
  })
}
