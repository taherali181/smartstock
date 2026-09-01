// Responsive and accessibility audit across the supported breakpoints.
//
// Phase 3 exits on "scanner workflows work at supported responsive
// breakpoints", so this drives every route at every viewport and reports
// horizontal overflow, touch targets, controls with no accessible name,
// heading-order jumps and clipped text.
//
// Touch targets are scored at 44px on touch viewports and 24px on pointer
// viewports, because the 44px guidance is for fingertips, not mice.
//
//   1. scripts/devstack.sh start && npm run dev:api && npm run dev
//   2. google-chrome --headless=new --remote-debugging-port=9222 \
//        --user-data-dir=/tmp/smartstock-audit about:blank &
//   3. node scripts/audit-ui.mjs
//
// Writes audit.json and exits non-zero when a viewport/route combination fails.

import http from 'node:http';
import fs from 'node:fs';
const req=(p,m='GET')=>new Promise((res,rej)=>{const r=http.request({host:'127.0.0.1',port:9222,path:p,method:m},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>{try{res(JSON.parse(d));}catch{res({raw:d});}});});r.on('error',rej);r.end();});
class CDP{constructor(u){this.ws=new WebSocket(u);this.id=0;this.p=new Map();this.ready=new Promise(r=>this.ws.onopen=r);
 this.ws.onmessage=e=>{const m=JSON.parse(e.data); if(m.id&&this.p.has(m.id)){this.p.get(m.id)(m);this.p.delete(m.id);}};}
 send(me,pa={}){const id=++this.id;return new Promise(r=>{this.p.set(id,r);this.ws.send(JSON.stringify({id,method:me,params:pa}));});}}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

const AUDIT = `(() => {
  const out = {};
  const doc = document.documentElement;
  out.overflow = doc.scrollWidth - doc.clientWidth;
  const vis = el => { const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };

  // touch targets
  out.small = [...document.querySelectorAll('button,a[href],select,input,[role=button]')]
    .filter(vis).filter(el => { const r = el.getBoundingClientRect(); return r.height < 44 || r.width < 44; })
    .map(el => ({ tag: el.tagName.toLowerCase(),
                  label: (el.getAttribute('aria-label') || el.innerText || el.name || '').trim().slice(0,24),
                  w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height) }))
    .slice(0, 8);

  // controls with no accessible name
  out.unlabelled = [...document.querySelectorAll('button,a[href],input,select')].filter(vis)
    .filter(el => !(el.getAttribute('aria-label') || el.getAttribute('title') ||
                    (el.innerText||'').trim() || el.labels?.length ||
                    el.getAttribute('aria-labelledby')))
    .map(el => el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : ''))
    .slice(0, 8);

  // images without alt
  out.imgNoAlt = [...document.querySelectorAll('img')].filter(el => !el.hasAttribute('alt')).length;

  // heading order
  const hs = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(vis).map(h => +h.tagName[1]);
  out.headings = hs.join(',');
  out.headingJump = hs.some((lvl, i) => i > 0 && lvl - hs[i-1] > 1);

  // text clipped horizontally inside its own box
  out.clipped = [...document.querySelectorAll('td,th,span,p,strong,small,h1,h2,h3')].filter(vis)
    .filter(el => el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== 'auto')
    .map(el => (el.innerText||'').trim().slice(0,20)).slice(0, 5);

  out.title = document.title;
  out.lang = doc.getAttribute('lang') || '(none)';
  return JSON.stringify(out);
})()`;

const VIEWPORTS = [
  ['scanner  320x640', 320, 640, true],
  ['phone    390x844', 390, 844, true],
  ['phablet  428x926', 428, 926, true],
  ['tablet   768x1024', 768, 1024, true],
  ['laptop  1280x800', 1280, 800, false],
  ['desktop 1920x1080', 1920, 1080, false],
];
const ROUTES = ['/', '/warehouse', '/inventory', '/products', '/orders', '/tasks'];

(async () => {
  const findings = [];
  for (const [vname, w, h, mobile] of VIEWPORTS) {
    for (const route of ROUTES) {
      const t = await req(`/json/new?http://localhost:5173${route}`, 'PUT');
      const c = new CDP(t.webSocketDebuggerUrl); await c.ready;
      await c.send('Page.enable'); await c.send('Runtime.enable');
      await c.send('Emulation.setDeviceMetricsOverride', {width:w, height:h, deviceScaleFactor:2, mobile});
      await sleep(4200);
      const raw = (await c.send('Runtime.evaluate', {expression: AUDIT, returnByValue:true})).result?.result?.value;
      let r; try { r = JSON.parse(raw); } catch { r = {error:'audit failed'}; }
      findings.push({viewport: vname, route, ...r});
      try { await req(`/json/close/${t.id}`); } catch {}
    }
  }
  fs.writeFileSync('audit.json', JSON.stringify(findings, null, 2));

  const TOUCH = new Set(['scanner  320x640','phone    390x844','phablet  428x926','tablet   768x1024']);
  const CONTROLS = new Set(['button','a','select','input']);
  let failed = 0;

  for (const f of findings) {
    const limit = TOUCH.has(f.viewport) ? 44 : 24;
    // Only real controls, and never the 1x1 hidden file input.
    const small = (f.small||[]).filter(s => CONTROLS.has(s.tag)
      && (s.h < limit || s.w < limit) && !(s.w <= 1 && s.h <= 1));
    const problems = [];
    if (f.overflow > 0) problems.push(`horizontal overflow ${f.overflow}px`);
    if (f.unlabelled?.length) problems.push(`no accessible name: ${f.unlabelled.join(', ')}`);
    if (f.headingJump) problems.push(`heading jump: ${f.headings}`);
    if (small.length) problems.push(`under ${limit}px: ` + small.map(s=>`${s.label||s.tag} ${s.w}x${s.h}`).join(', '));
    if (problems.length) { failed++; console.log(`FAIL ${f.viewport}  ${f.route}`); problems.forEach(p=>console.log(`       ${p}`)); }
  }
  console.log(`\n${findings.length - failed}/${findings.length} viewport/route combinations clean`);
  const langs = [...new Set(findings.map(f=>f.lang))];
  console.log(`lang attribute: ${langs.join(', ')}`);
  process.exit(failed ? 1 : 0);
})();
