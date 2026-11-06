// gammapdf.com — the little behaviour the page needs. No dependencies.
//  - the mobile menu button
//  - "Download for <this OS>" on the primary buttons, and on the download
//    page the visitor's card marked and moved first
//  - the front page's clips: a still until one comes near, then the animation
//  - copy buttons on code blocks
//  - the latest release: version, per-OS asset links, star count (two calls
//    to api.github.com; every element keeps a working fallback link)
//  - on the pricing page, "Coming soon" for a plan Gamma Cloud does not sell
//    right now (one call to its /api/plans; a failure leaves the page as written)
(() => {
  const REPO = 'tim4431/Gamma';
  const STORE = 'https://apps.microsoft.com/detail/9N8WGWR2J2MV';
  const TESTFLIGHT = 'https://testflight.apple.com/join/nTmSHDx6';

  // Menu
  const menu = document.querySelector('.top__menu');
  const nav = document.getElementById('nav');
  menu?.addEventListener('click', () => {
    const open = nav.classList.toggle('nav--open');
    menu.setAttribute('aria-expanded', String(open));
  });
  const closeMenu = () => { nav?.classList.remove('nav--open'); menu?.setAttribute('aria-expanded', 'false'); };
  nav?.addEventListener('click', e => { if (e.target.closest('a')) closeMenu(); });
  // Escape, a click elsewhere, or focus leaving the header closes it too.
  addEventListener('keydown', e => {
    if (e.key === 'Escape' && nav?.classList.contains('nav--open')) { closeMenu(); menu.focus(); }
  });
  addEventListener('click', e => { if (!e.target.closest('.top')) closeMenu(); });
  document.querySelector('.top')?.addEventListener('focusout', e => {
    if (e.relatedTarget && !e.relatedTarget.closest('.top')) closeMenu();
  });

  // OS detection — the "platform" hints first, the user agent as a fallback.
  // An iPhone has no build: it keeps the plain "Download" of /download, and is
  // ruled out first because it says "like Mac OS X". An iPad's Safari says it
  // is a Mac; its touch points give it away. Android says it is Linux ("Linux;
  // Android", platform "Linux aarch64"), so it is asked about before those.
  const ua = navigator.userAgent;
  const platform = (navigator.userAgentData?.platform || navigator.platform || '').toLowerCase();
  const mac = /mac/.test(platform) || /Mac OS X/.test(ua);
  const os = /iPhone|iPod/.test(ua) ? null
    : /iPad/.test(ua) || (mac && navigator.maxTouchPoints > 1) ? 'ipad'
    : /android/.test(platform) || /Android/.test(ua) ? 'android'
    : /win/.test(platform) || /Windows/.test(ua) ? 'windows'
    : mac ? 'macos'
    : /linux/.test(platform) || /Linux/.test(ua) ? 'linux'
    : null;
  const label = { windows: 'Download for Windows', macos: 'Download for macOS', linux: 'Download for Linux', ipad: 'Get the iPad beta', android: 'Download for Android' };
  const name = () => document.querySelectorAll('[data-download-label]').forEach(el => { el.textContent = label[os]; });
  // Android is named only once a release carries its APK (below): until
  // then the buttons stay the plain "Download" of /download.
  if (os && os !== 'android') name();
  if (os) {
    const fixed = { windows: STORE, ipad: TESTFLIGHT }[os];
    if (fixed) document.querySelectorAll('[data-download]').forEach(a => { a.href = fixed; });
    // First in the DOM rather than by CSS order, so the keyboard follows the eye.
    const card = document.querySelector(`.dl__card[data-os="${os}"]`);
    if (card) { card.classList.add('dl__card--mine'); card.parentElement.prepend(card); }
  }

  // Clips: the still until one comes near, so a visit that stops at the top
  // never fetches them. Under reduced motion the <source> keeps the still,
  // and a clip that fails to load goes back to it.
  const clips = new IntersectionObserver(entries => entries.forEach(({ isIntersecting, target }) => {
    if (!isIntersecting) return;
    clips.unobserve(target);
    const still = target.src;
    target.addEventListener('error', () => { target.src = still; }, { once: true });
    target.src = target.dataset.clip;
  }), { rootMargin: '200px' });
  document.querySelectorAll('img[data-clip]').forEach(img => clips.observe(img));

  // Copy buttons
  document.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const text = btn.parentElement.querySelector('code')?.textContent ?? '';
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = 'Copied';
      } catch {
        btn.textContent = 'Select and copy';
      }
      setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
    });
  });

  // Latest release + stars. Silent on any failure (rate limit, offline).
  const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
  const assetFor = (release, test) => release?.assets?.find(a => test(a.name))?.browser_download_url;
  fetch(`https://api.github.com/repos/${REPO}/releases?per_page=30`, { headers: { Accept: 'application/vnd.github+json' } })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(releases => {
      const published = releases.filter(r => !r.draft && !r.prerelease);
      const desktop = published.find(r => /^v\d/.test(r.tag_name));
      if (desktop) {
        document.querySelectorAll('[data-version]').forEach(el => { el.textContent = desktop.tag_name; });
        document.querySelectorAll('[data-version-link]').forEach(a => { a.href = desktop.html_url; });
        const links = {
          exe: assetFor(desktop, n => /\.exe$/i.test(n)),
          dmg: assetFor(desktop, n => /\.dmg$/i.test(n) && !/arm64/i.test(n)) || assetFor(desktop, n => /\.dmg$/i.test(n)),
          deb: assetFor(desktop, n => /\.deb$/i.test(n)),
        };
        for (const [kind, url] of Object.entries(links)) {
          if (url) document.querySelectorAll(`[data-asset="${kind}"]`).forEach(a => { a.href = url; });
        }
        const mine = { macos: links.dmg, linux: links.deb }[os];
        if (mine) document.querySelectorAll('[data-download]').forEach(a => { a.href = mine; });
      }
      // The Connector zip rides on the desktop release; older ones had their own extension-v* release.
      const zip = published.map(r => assetFor(r, n => /^gamma-connector-.*\.zip$/i.test(n))).find(Boolean);
      if (zip) document.querySelectorAll('[data-asset="extension"]').forEach(a => { a.href = zip; });
      // The Android APK rides on the release too, when that release's build
      // was signed; the newest release that carries one.
      const apk = published.map(r => assetFor(r, n => /\.apk$/i.test(n))).find(Boolean);
      if (apk) {
        document.querySelectorAll('[data-asset="apk"]').forEach(a => { a.href = apk; });
        if (os === 'android') {
          name();
          document.querySelectorAll('[data-download]').forEach(a => { a.href = apk; });
        }
      }
    })
    .catch(() => {});
  fetch(`https://api.github.com/repos/${REPO}`, { headers: { Accept: 'application/vnd.github+json' } })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(repo => {
      const n = repo.stargazers_count;
      if (!Number.isFinite(n)) return;
      document.querySelectorAll('[data-stars]').forEach(el => { el.textContent = fmt(n); el.hidden = false; });
    })
    .catch(() => {});

  // Plans on sale: the account server says which paid plans can be bought now.
  const choose = document.querySelectorAll('[data-plan]');
  if (choose.length) {
    fetch('https://account.gammapdf.com/api/plans')
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(d => choose.forEach(a => {
        if (d.plans?.[a.dataset.plan]?.on_sale !== false) return;
        const soon = document.createElement('span');
        soon.className = 'btn btn--ghost';
        soon.setAttribute('aria-disabled', 'true');
        soon.textContent = 'Coming soon';
        a.replaceWith(soon);
      }))
      .catch(() => {});
  }
})();

// A page's pictures carry no size, so the browser's own jump to #anchor can
// land above the heading once they have loaded: jump again when they have.
if (location.hash.length > 1) {
  addEventListener('load', () => {
    try { document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView(); } catch {}
  });
}
