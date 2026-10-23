'use client'

import { motion, useMotionValue, useReducedMotion, useSpring } from 'framer-motion'
import { ArrowRight, Check, CircleHelp, Clock3, Code2, IndianRupee, Menu, Mic, Phone, PhoneOff, ShieldCheck, X } from 'lucide-react'
import { useEffect, useState } from 'react'

const steps = [
  { number: '01', title: 'Order placed', text: 'Send us the order with one clean API call.', icon: Code2 },
  { number: '02', title: 'VoRoute calls', text: 'A natural Hinglish call goes out instantly.', icon: Phone },
  { number: '03', title: 'Customer answers', text: 'They say haan, nahi, or something unclear.', icon: Mic },
  { number: '04', title: 'You ship smart', text: 'Only confirmed orders move to dispatch.', icon: ShieldCheck },
]

const outcomes = [
  { label: 'CONFIRMED', title: 'Clear yes, ship it.', text: 'A real, positive intent signal from the customer.', color: 'green', icon: Check },
  { label: 'DECLINED', title: 'Clear no, a return saved.', text: 'Stop the parcel before it becomes a costly loop.', color: 'red', icon: X },
  { label: 'UNCLEAR', title: 'No clear answer, you decide.', text: 'It never guesses. Ambiguity stays visible.', color: 'amber', icon: CircleHelp },
]

function Reveal({ children, delay = 0, className = '' }: { children: React.ReactNode; delay?: number; className?: string }) {
  return <motion.div initial={false} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.15 }} transition={{ duration: 0.65, delay, ease: [0.22, 1, 0.36, 1] }} className={className}>{children}</motion.div>
}

function formatCallTime(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0')
  const seconds = (totalSeconds % 60).toString().padStart(2, '0')
  return `${minutes}:${seconds}`
}

function LiveWaveform() {
  const barCount = 42
  const activeBars = 25
  const [bars, setBars] = useState(() => Array.from({ length: barCount }, (_, index) => index < activeBars ? 10 + ((index * 17) % 27) : 2))

  useEffect(() => {
    let animationFrame = 0
    let lastShift = performance.now()
    let previousHeight = bars[activeBars - 1]

    const animate = (now: number) => {
      if (now - lastShift > 115) {
        lastShift = now
        setBars((current) => {
          const nextHeight = Math.max(5, Math.min(40, previousHeight * 0.62 + (5 + Math.random() * 35) * 0.38))
          previousHeight = nextHeight
          return [...current.slice(1, activeBars), nextHeight, ...current.slice(activeBars)]
        })
      }
      animationFrame = window.requestAnimationFrame(animate)
    }

    animationFrame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(animationFrame)
  }, [])

  return <div className="wave" aria-label="Active audio waveform">{bars.map((height, index) => <i key={index} style={{ height: `${height}px` }} />)}</div>
}

function LiveCall() {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setElapsed((current) => current + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const rotateX = useSpring(y, { stiffness: 160, damping: 20 })
  const rotateY = useSpring(x, { stiffness: 160, damping: 20 })
  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    x.set((event.clientX - rect.left) / rect.width * 8 - 4)
    y.set(-((event.clientY - rect.top) / rect.height * 8 - 4))
  }
  return <motion.div className="call-scene" onMouseMove={onMove} onMouseLeave={() => { x.set(0); y.set(0) }} style={{ rotateX, rotateY, transformPerspective: 1000 }}>
    <div className="rings" aria-hidden="true"><span /><span /><span /></div>
    <div className="call-card">
      <div className="call-top"><span className="live-dot" /> <span>LIVE CALL</span><span className="call-time">{formatCallTime(elapsed)}</span></div>
      <div className="caller"><div className="avatar">RK</div><div><strong>Rahul Kumar</strong><p>+91 98••• ••421</p></div><div className="signal"><span /><span /><span /><span /></div></div>
      <LiveWaveform />
      <div className="quote"><span>“</span><p>Haan ji, confirm kar dijiye.”</p></div>
      <div className="call-bottom"><span className="encrypted"><ShieldCheck size={13} /> Encrypted call</span><span className="hangup" aria-hidden="true"><PhoneOff size={18} /></span><span className="duration">{formatCallTime(elapsed)}</span></div>
    </div>
  </motion.div>
}

export default function Page() {
  const [menuOpen, setMenuOpen] = useState(false)
  return <main className="site-shell">
    <nav className="nav wrap"><a href="#top" className="wordmark">Vo<span>Route</span></a><div className={`nav-links ${menuOpen ? 'open' : ''}`}><a href="#how">How it works</a><a href="#outcomes">Outcomes</a><a href="#why">Why VoRoute</a><a href="https://linkedin.com/in/aymishra01" target="_blank" rel="noopener noreferrer" className="mobile-cta">Get a free pilot <ArrowRight size={15} /></a></div><div className="nav-actions"><a href="https://voroute.onrender.com/login" target="_blank" rel="noopener noreferrer" className="login">Log in</a><a href="https://linkedin.com/in/aymishra01" target="_blank" rel="noopener noreferrer" className="button button-small">Get a free pilot <ArrowRight size={15} /></a><button className="menu-button" aria-label="Toggle navigation" onClick={() => setMenuOpen(!menuOpen)}><Menu size={20} /></button></div></nav>
    <section id="top" className="hero wrap"><div className="hero-copy"><Reveal><div className="eyebrow"><span className="eyebrow-line" /> VOICE CONFIRMATION FOR COD</div><h1>Ship orders.<br /><em>Not returns.</em></h1><p className="hero-sub">Most RTO isn&apos;t a delivery problem. It&apos;s an intent problem. VoRoute confirms every COD order in Hinglish, before it ships — so your team knows what&apos;s coming, and what isn&apos;t.</p><div className="hero-actions"><a href="https://linkedin.com/in/aymishra01" target="_blank" rel="noopener noreferrer" className="button">Get a free pilot <ArrowRight size={17} /></a><a href="#how" className="text-link">See how it works <ArrowRight size={16} /></a></div><div className="trust"><span><ShieldCheck size={15} /> Built for Indian D2C</span><span className="trust-divider" /><span><Clock3 size={15} /> Confirm in minutes</span></div></Reveal></div><Reveal delay={0.16} className="hero-visual"><LiveCall /></Reveal></section>
    <div className="ticker" aria-label="Stop RTO before it starts. Hindi-first voice AI. Ship with confidence."><div className="ticker-inner"><div className="ticker-group"><span>Stop RTO before it starts</span><i aria-hidden="true" /><span>Hindi-first voice AI</span><i aria-hidden="true" /><span>Ship with confidence</span><i aria-hidden="true" /></div><div className="ticker-group" aria-hidden="true"><span>Stop RTO before it starts</span><i /><span>Hindi-first voice AI</span><i /><span>Ship with confidence</span><i /></div></div></div>
    <section id="why" className="problem section wrap"><Reveal className="section-intro"><div className="eyebrow"><span className="eyebrow-line" /> THE COST OF UNCERTAINTY</div><h2>COD returns bleed money.<br /><span>Every time.</span></h2></Reveal><div className="problem-grid"><Reveal delay={0.1}><p className="large-copy">The parcel ships. The customer flakes. It comes back. <strong>Pure loss both ways.</strong></p></Reveal><Reveal delay={0.18}><div className="loss-card"><div className="loss-route"><div className="route-point active"><span /><small>Brand</small></div><div className="route-line"><span>ships</span></div><div className="route-point"><span /><small>Doorstep</small></div><div className="route-line return"><span>returns</span></div><div className="route-point muted"><span /><small>Warehouse</small></div></div><div className="loss-bottom"><span>Average loss per RTO</span><strong><IndianRupee size={20} />561</strong></div></div></Reveal></div></section>
    <section id="how" className="steps section wrap"><Reveal className="section-intro centered"><div className="eyebrow"><span className="eyebrow-line" /> HOW IT WORKS</div><h2>From order to <span>intent.</span></h2><p>One API call. Four simple steps. No more guessing games.</p></Reveal><div className="step-grid">{steps.map((step, i) => { const Icon = step.icon; return <Reveal key={step.number} delay={i * 0.08}><div className="step-card"><div className="step-head"><span>{step.number}</span><Icon size={20} /></div><h3>{step.title}</h3><p>{step.text}</p>{i < steps.length - 1 && <ArrowRight className="step-arrow" size={18} />}</div></Reveal> })}</div></section>
    <section id="outcomes" className="outcomes section wrap"><Reveal className="section-intro"><div className="eyebrow"><span className="eyebrow-line" /> NO BLACK BOXES</div><h2>Every answer has<br /><span>a clear next step.</span></h2><p>VoRoute never guesses. A confirm is always a real yes.</p></Reveal><div className="outcome-grid">{outcomes.map((outcome, i) => { const Icon = outcome.icon; return <Reveal key={outcome.label} delay={i * 0.1}><div className={`outcome-card ${outcome.color}`}><div className="outcome-icon"><Icon size={19} /></div><span className="outcome-label">{outcome.label}</span><h3>{outcome.title}</h3><p>{outcome.text}</p><ArrowRight className="outcome-arrow" size={18} /></div></Reveal> })}</div></section>
    <section className="stats"><div className="wrap stat-grid"><Reveal><div className="stat"><strong>20–35%</strong><span>of COD orders return as RTO</span></div></Reveal><Reveal delay={0.08}><div className="stat"><strong>~₹561</strong><span>lost per returned order</span></div></Reveal><Reveal delay={0.16}><div className="stat"><strong>Before dispatch</strong><span>confirmed, not assumed</span></div></Reveal><Reveal delay={0.24}><div className="stat"><strong>Hindi / Hinglish</strong><span>the language customers trust</span></div></Reveal></div></section>
    <section id="pilot" className="final-cta wrap"><Reveal><div className="cta-glow" /><div className="eyebrow"><span className="eyebrow-line" /> READY TO SHIP SMARTER?</div><h2>Run it free<br /><span>on your orders.</span></h2><p>See what confirmed intent can do for your RTO rate. Your first pilot is on us.</p><a href="https://linkedin.com/in/aymishra01" target="_blank" rel="noopener noreferrer" className="button">Get a free pilot <ArrowRight size={17} /></a></Reveal></section>
    <footer className="footer wrap"><a href="#top" className="wordmark">Vo<span>Route</span></a><span>© 2025 VoRoute. Built for better deliveries.</span><a href="mailto:support@voroute.com">support@voroute.com</a></footer>
  </main>
}
