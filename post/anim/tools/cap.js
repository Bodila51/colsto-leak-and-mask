// Frame capture: N Playwright pages in parallel, each calling window.renderAt(t) and taking a screenshot.
//   node cap.js --layout landscape|vertical|tall|panel --out DIR [--from 0 --to 600 | --times 0,5,10] [--png] [--workers 8]
//   node cap.js --page terminal.html --layout term|termv ...   (the triptych's terminal strip)
// Serves post/anim over a throw-away local http server (fetch() does not work from file://).
const fs = require('fs'), path = require('path'), http = require('http');
const argv = process.argv.slice(2), arg = (k, d) => { const i = argv.indexOf('--' + k); return i < 0 ? d : (argv[i + 1] === undefined || argv[i + 1].startsWith('--') ? true : argv[i + 1]); };
const layout = arg('layout', 'landscape'), out = path.resolve(arg('out', 'frames')), png = argv.includes('--png');
const N = +arg('workers', 8), FPS = 30;
const times = arg('times', null) ? String(arg('times')).split(',').map(Number) : null;
const f0 = +arg('from', 0), f1 = +arg('to', 600);
const page = arg('page', 'index.html'), query = arg('query', '');     // --query 'data=midway_terminal' (extra URL parameters)
const size = { vertical: { width: 1080, height: 742 }, tall: { width: 1080, height: 1350 }, panel: { width: 960, height: 540 }, panelv: { width: 1080, height: 500 },
  term: { width: 1920, height: 540 }, termv: { width: 1080, height: 242 }, overlayv: { width: 1080, height: 1350 } }[layout] || { width: 1920, height: 1080 };
const clear = layout.startsWith('overlay') ? { omitBackground: true } : {};   // transparent PNG for ffmpeg overlay
const root = path.resolve(__dirname, '..');

function loadPlaywright() {
  const tries = [path.join(__dirname, 'node_modules', 'playwright'), 'playwright',
    path.join(process.env.USERPROFILE || '', 'Downloads', 'legends-video', 'tools', 'node_modules', 'playwright')];
  for (const t of tries) { try { return require(t); } catch (e) { /* next */ } }
  console.error('playwright not found: run "npm install" in post/anim/tools'); process.exit(2);
}
const { chromium } = loadPlaywright();
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.woff2': 'font/woff2' };

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const srv = http.createServer((q, r) => {
    if (q.url.startsWith('/favicon')) { r.writeHead(204); return r.end(); }
    const p = path.join(root, decodeURIComponent(q.url.split('?')[0]));
    if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { r.writeHead(404); return r.end(); }
    r.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' }); fs.createReadStream(p).pipe(r);
  }).listen(0, '127.0.0.1');
  await new Promise(r => srv.on('listening', r));
  const url = `http://127.0.0.1:${srv.address().port}/${page}?layout=${layout}${query ? '&' + query : ''}`;
  const exe = process.env.LOCALAPPDATA + '/ms-playwright/chromium-1248/chrome-win64/chrome.exe';
  const br = await chromium.launch(fs.existsSync(exe) ? { executablePath: exe } : {});
  const jobs = [];
  const total = times ? times.length : f1 - f0;
  let done = 0; const t0 = Date.now();
  for (let w = 0; w < N; w++) jobs.push((async () => {
    const p = await br.newPage({ viewport: size, deviceScaleFactor: 1 });
    p.on('pageerror', e => { console.error('page error:', e.message); process.exitCode = 3; });
    p.on('console', m => { if (m.type() === 'error') console.error('console:', m.text()); });
    await p.goto(url); await p.evaluate(() => window.ready);
    const one = async (t, file, o) => { await p.evaluate(x => renderAt(x), t); await p.screenshot({ path: file, ...o }); done++; };
    if (times) for (let i = w; i < times.length; i += N) await one(times[i], path.join(out, `t${String(times[i]).replace('.', '_')}.png`), { type: 'png', ...clear });
    else for (let f = f0 + w; f < f1; f += N) await one(f / FPS, path.join(out, `f${String(f).padStart(4, '0')}.${png ? 'png' : 'jpg'}`), png ? { type: 'png' } : { type: 'jpeg', quality: 95 });
  })());
  await Promise.all(jobs); await br.close(); srv.close();
  console.log(`[cap] ${layout}: ${total} frames in ${((Date.now() - t0) / 1000).toFixed(1)}s -> ${out}`);
})();
