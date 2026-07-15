const chat = document.getElementById('chat');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const micBtn = document.getElementById('micBtn');
const core = document.getElementById('core');
// coreStatus removed — core animation zone hidden from UI
const settingsBtn = document.getElementById('settingsBtn');
const sheetOverlay = document.getElementById('sheetOverlay');
const sheetClose = document.getElementById('sheetClose');
const keyForm = document.getElementById('keyForm');
const keyList = document.getElementById('keyList');
const menuBtn = document.getElementById('menuBtn');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const sidebarCloseBtn = document.getElementById('sidebarCloseBtn');
const newChatBtn = document.getElementById('newChatBtn');
const chatListEl = document.getElementById('chatList');

let currentChatId = null;
let pendingFiles = [];

// setCoreState — core element optional (hidden in new UI)
function setCoreState(state, statusText) { if (core) core.className = `core ${state}`; }
setCoreState('idle', 'Taiyaar hoon');

// ---------- Markdown-lite renderer (Claude-style) ----------
function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function renderMarkdownLite(text) {
  const parts = [];
  // Split on fenced code blocks
  const codeRe = /```(\w*)\n?([\s\S]*?)```/g;
  let last = 0, m;
  while ((m = codeRe.exec(text)) !== null) {
    if (m.index > last) parts.push({ type: 'text', content: text.slice(last, m.index) });
    parts.push({ type: 'code', lang: m[1] || 'code', content: m[2] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ type: 'text', content: text.slice(last) });

  return parts.map(part => {
    if (part.type === 'code') {
      const id = 'cb_' + Math.random().toString(36).slice(2, 8);
      const escaped = escapeHtml(part.content.trimEnd());
      return `<div class="code-block-wrap">
        <div class="code-block-header">
          <span class="code-lang-label">${escapeHtml(part.lang)}</span>
          <button class="copy-code-btn" onclick="(function(b){b.textContent='✅ Copied';navigator.clipboard.writeText(document.getElementById('${id}').textContent);setTimeout(()=>b.textContent='📋 Copy',1400);})(this)">📋 Copy</button>
        </div>
        <pre id="${id}">${escaped}</pre>
      </div>`;
    } else {
      // Inline: **bold**, `code`, line breaks
      let html = escapeHtml(part.content);
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/`([^`]+)`/g, '<code style="background:#1a1f2e;padding:2px 5px;border-radius:4px;font-family:monospace;font-size:13px;">$1</code>');
      html = html.replace(/\n/g, '<br>');
      return `<span>${html}</span>`;
    }
  }).join('');
}

// ---------- HLS (.m3u8) in-chat player ----------
// Registry — multiple streams ek saath chat mein chal sakti hain. Har
// entry: { id, hls, video, wrapper, retries }. pause/resume/stop bina
// 'sab'/'all' ke hamesha SABSE RECENT (last) active player par apply
// hote hain; stop_all_streams sabko band karta hai.
window._jarvisHlsPlayers = [];
window._jarvisHls = { hls: null, video: null, wrapper: null }; // backward-compat alias, last player

const HLS_MAX_RETRIES = 4;

function _hlsSetActive(entry) {
  window._jarvisHls = entry;
}

function _hlsRemoveEntry(entry) {
  const i = window._jarvisHlsPlayers.indexOf(entry);
  if (i !== -1) window._jarvisHlsPlayers.splice(i, 1);
  if (window._jarvisHls === entry) {
    const last = window._jarvisHlsPlayers[window._jarvisHlsPlayers.length - 1];
    window._jarvisHls = last || { hls: null, video: null, wrapper: null };
  }
}

function renderHlsPlayer(item) {
  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'width:100%;max-width:360px;border-radius:14px;overflow:hidden;border:1px solid var(--line);background:#000;margin:4px 0;position:relative;';

  const cap = document.createElement('div');
  cap.style.cssText = 'background:#111;color:#ccc;font-size:11px;padding:6px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:6px;justify-content:space-between;';
  const capLeft = document.createElement('span');
  capLeft.style.cssText = 'display:flex;align-items:center;gap:6px;overflow:hidden;text-overflow:ellipsis;';
  capLeft.innerHTML = '<span style="width:7px;height:7px;border-radius:50%;background:#ff4d4d;display:inline-block;flex-shrink:0;"></span><span>' + (item.title || 'Live Stream') + '</span>';
  cap.appendChild(capLeft);

  // Quality selector — hls.js levels parse hone ke baad populate hota hai
  const qualitySelect = document.createElement('select');
  qualitySelect.style.cssText = 'display:none;background:#222;color:#ccc;border:1px solid #444;border-radius:6px;font-size:10px;padding:2px 4px;';
  cap.appendChild(qualitySelect);
  wrapper.appendChild(cap);

  const videoBox = document.createElement('div');
  videoBox.style.cssText = 'position:relative;width:100%;';

  const vid = document.createElement('video');
  vid.style.cssText = 'width:100%;aspect-ratio:16/9;display:block;background:#000;';
  vid.controls = true;
  vid.playsInline = true;
  vid.autoplay = true;
  vid.muted = false;
  videoBox.appendChild(vid);

  // Reconnecting indicator overlay
  const reconnectBox = document.createElement('div');
  reconnectBox.style.cssText = 'display:none;position:absolute;inset:0;align-items:center;justify-content:center;background:rgba(0,0,0,0.55);color:#fff;font-size:12px;flex-direction:column;gap:6px;';
  reconnectBox.innerHTML = '<span>🔄 Reconnect ho raha hai...</span>';
  videoBox.appendChild(reconnectBox);

  wrapper.appendChild(videoBox);

  const errBox = document.createElement('div');
  errBox.style.cssText = 'display:none;padding:16px;text-align:center;background:#111;color:#aaa;font-size:12px;';
  errBox.textContent = '⚠️ Stream load nahi hui — URL ya network check karo.';
  wrapper.appendChild(errBox);

  // ── Custom control bar: PiP / mute / fullscreen ──
  const ctrlBar = document.createElement('div');
  ctrlBar.style.cssText = 'position:absolute;bottom:8px;right:8px;display:flex;gap:6px;z-index:5;';

  function makeCtrlBtn(label, title) {
    const b = document.createElement('button');
    b.textContent = label;
    b.title = title;
    b.style.cssText = 'background:rgba(0,0,0,0.6);color:#fff;border:1px solid rgba(255,255,255,0.25);border-radius:8px;padding:4px 7px;font-size:12px;cursor:pointer;line-height:1;';
    return b;
  }

  const muteBtn = makeCtrlBtn('🔊', 'Mute/Unmute');
  muteBtn.onclick = (e) => {
    e.stopPropagation();
    vid.muted = !vid.muted;
    muteBtn.textContent = vid.muted ? '🔇' : '🔊';
  };

  const pipBtn = makeCtrlBtn('⧉', 'Picture-in-Picture');
  pipBtn.onclick = async (e) => {
    e.stopPropagation();
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else if (document.pictureInPictureEnabled) {
        await vid.requestPictureInPicture();
      }
    } catch (err) { /* PiP not supported on this device/browser — silent no-op */ }
  };

  const fsBtn = makeCtrlBtn('⛶', 'Fullscreen');
  fsBtn.onclick = (e) => {
    e.stopPropagation();
    try {
      if (document.fullscreenElement) document.exitFullscreen();
      else videoBox.requestFullscreen();
    } catch (err) { /* fullscreen not supported — silent no-op */ }
  };

  ctrlBar.appendChild(muteBtn);
  if (document.pictureInPictureEnabled) ctrlBar.appendChild(pipBtn);
  ctrlBar.appendChild(fsBtn);
  videoBox.appendChild(ctrlBar);

  const proxiedUrl = '/api/hlsproxy?url=' + encodeURIComponent(item.src);
  const entry = { id: Date.now() + Math.random(), hls: null, video: vid, wrapper, retries: 0 };

  function showError() {
    reconnectBox.style.display = 'none';
    vid.style.display = 'none';
    errBox.style.display = 'block';
  }

  function showReconnecting(show) {
    reconnectBox.style.display = show ? 'flex' : 'none';
  }

  function setupQualityMenu(hls) {
    if (!hls.levels || hls.levels.length < 2) return; // ek hi quality — selector ki zarurat nahi
    qualitySelect.innerHTML = '';
    const autoOpt = document.createElement('option');
    autoOpt.value = '-1'; autoOpt.textContent = 'Auto';
    qualitySelect.appendChild(autoOpt);
    hls.levels.forEach((lvl, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = lvl.height ? lvl.height + 'p' : Math.round((lvl.bitrate || 0) / 1000) + 'kbps';
      qualitySelect.appendChild(opt);
    });
    qualitySelect.value = '-1';
    qualitySelect.style.display = 'inline-block';
    qualitySelect.onchange = () => { hls.currentLevel = parseInt(qualitySelect.value, 10); };
  }

  if (window.Hls && window.Hls.isSupported()) {
    const hls = new window.Hls({ maxBufferLength: 30 });
    entry.hls = hls;

    hls.on(window.Hls.Events.MANIFEST_PARSED, () => setupQualityMenu(hls));

    hls.on(window.Hls.Events.ERROR, (evt, data) => {
      if (!data || !data.fatal) return;

      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        if (entry.retries < HLS_MAX_RETRIES) {
          entry.retries++;
          showReconnecting(true);
          setTimeout(() => { try { hls.startLoad(); } catch (e) {} }, 1500 * entry.retries);
        } else if (!hls._triedDirect) {
          // Proxy se baar-baar fail — seedha original URL try karo (kai
          // public HLS servers CORS allow karte hain, proxy zaroori nahi hoti)
          hls._triedDirect = true;
          entry.retries = 0;
          hls.loadSource(item.src);
        } else {
          showReconnecting(false);
          showError();
        }
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        try { hls.recoverMediaError(); } catch (e) { showError(); }
      } else {
        showError();
      }
    });

    hls.on(window.Hls.Events.FRAG_LOADED, () => { entry.retries = 0; showReconnecting(false); });

    hls.loadSource(proxiedUrl);
    hls.attachMedia(vid);
  } else if (vid.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari/iOS — native HLS support, proxy/hls.js ki zarurat nahi
    vid.src = item.src;
    qualitySelect.style.display = 'none';
  } else {
    showError();
  }

  vid.onerror = () => { if (!entry.hls) showError(); };

  window._jarvisHlsPlayers.push(entry);
  _hlsSetActive(entry);

  return wrapper;
}

function handleHlsControl(action) {
  if (action === 'stopall') {
    [...window._jarvisHlsPlayers].forEach(entry => _stopHlsEntry(entry));
    return;
  }
  const state = window._jarvisHls;
  if (!state || !state.video) return;
  if (action === 'pause') state.video.pause();
  else if (action === 'resume') state.video.play().catch(() => {});
  else if (action === 'stop') _stopHlsEntry(state);
  // 'status' ke liye koi client action nahi — text response mein hi batya jaata hai
}

function _stopHlsEntry(entry) {
  if (!entry) return;
  try { entry.video.pause(); } catch (e) {}
  if (entry.hls) { try { entry.hls.destroy(); } catch (e) {} }
  if (entry.wrapper && entry.wrapper.parentNode) entry.wrapper.remove();
  _hlsRemoveEntry(entry);
}


// ---------- YouTube ID ----------
function getYouTubeId(url) {
  const patterns = [/(?:youtube\.com\/watch\?v=)([a-zA-Z0-9_-]{11})/,/(?:youtu\.be\/)([a-zA-Z0-9_-]{11})/,/(?:youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})/,/(?:youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})/];
  for (const re of patterns) { const m = url.match(re); if (m) return m[1]; }
  return null;
}

// ---------- Add message ----------
function addMessage(text, who, attachments) {
  const msg = document.createElement('div');
  msg.className = `msg ${who}`;
  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = who === 'user' ? 'AAP' : 'JARVIS';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';

  if (attachments && attachments.length > 0 && who === 'user') {
    const attDiv = document.createElement('div');
    attDiv.className = 'attach-preview';
    attachments.forEach(a => {
      if (a.type === 'image') {
        const img = document.createElement('img');
        img.src = a.base64; img.className = 'attach-thumb';
        attDiv.appendChild(img);
      } else {
        const chip = document.createElement('div');
        chip.className = 'attach-chip';
        chip.innerHTML = `<span>${a.type === 'pdf' ? '📄' : '📎'}</span><span>${a.name}</span>`;
        attDiv.appendChild(chip);
      }
    });
    bubble.appendChild(attDiv);
  }

  let workingText = text || '';
  const mediaItems = [];
  const genMatch = workingText.match(/IMAGE_GENERATED:(\/static\/generated\/[a-zA-Z0-9_.\-]+)/);
  if (genMatch) { mediaItems.push({ type: 'image', src: genMatch[1] }); workingText = workingText.replace(genMatch[0], '').trim(); }
  // Markdown image wrappers hata do agar Groq ne wrap kiya ho: ![...](url) ya [url](url)
  workingText = workingText.replace(/!\[.*?\]\((https?:\/\/[^)]+)\)/g, (m, url) => { mediaItems.push({ type: 'image', src: url }); return ''; });
  workingText = workingText.replace(/IMAGE_FOUND:\s*(\S+)/g, (m, url) => { mediaItems.push({ type: 'image', src: url.replace(/[`*_]/g,'') }); return ''; });
  workingText = workingText.replace(/VIDEO_FOUND:\s*(\S+?)\|([^\n]*)/g, (m, url, title) => { mediaItems.push({ type: 'video', src: url.replace(/[`*_]/g,''), title: title.trim() }); return ''; });
  // HLS (.m3u8) stream — chat ke andar hi hls.js se play hoga
  workingText = workingText.replace(/HLS_FOUND:\s*(\S+?)\|([^\n]*)/g, (m, url, title) => { mediaItems.push({ type: 'hls', src: url.replace(/[`*_]/g,''), title: title.trim() }); return ''; });
  // HLS control commands (pause/resume/stop/status) — koi visible media nahi,
  // sirf currently-loaded stream player par action perform karna hai
  const hlsControls = [];
  workingText = workingText.replace(/HLS_CONTROL:\s*(stopall|status|resume|pause|stop)/g, (m, action) => { hlsControls.push(action); return ''; });
  // Agar Groq ne IMAGE_FOUND ke baad sirf URL mention kiya (bina prefix ke)
  workingText = workingText.replace(/\b(https?:\/\/[^\s]+\.(?:jpg|jpeg|png|gif|webp))\b/gi, (m, url) => { if(!mediaItems.find(i=>i.src===url)) { mediaItems.push({ type: 'image', src: url }); return ''; } return m; });
  // RADIO_STREAM blocks extract karo
  const radioItems = [];
  workingText = workingText.replace(/RADIO_STREAM:([^|\n]+)\|([^\n]+)/g, (m, url, name) => {
    radioItems.push({ url: url.trim(), name: name.trim() });
    return '';
  });

  // ZIP download project token
  let showZipDownload = false;
  if (workingText.includes('ZIP_DOWNLOAD_PROJECT')) {
    showZipDownload = true;
    workingText = workingText.replace(/ZIP_DOWNLOAD_PROJECT/g, '').trim();
  }

  // FILE_CREATE blocks extract karo
  const fileBlocks = [];
  workingText = workingText.replace(/FILE_CREATE:([^\n]+)\n([\s\S]*?)\nFILE_END/g, (m, fname, fcontent) => {
    fileBlocks.push({ filename: fname.trim(), content: fcontent });
    return '';
  });
  workingText = workingText.trim();

  if (workingText) {
    // Markdown-lite render: code blocks + bold + inline code
    const rendered = renderMarkdownLite(workingText);
    const p = document.createElement('div');
    p.innerHTML = rendered;
    if (mediaItems.length) p.style.marginBottom = '8px';
    bubble.appendChild(p);
  }

  if (mediaItems.length) {
    const isCarousel = mediaItems.length > 1;
    // Chat bubble normally shrink-to-fit hoti hai (align-items:flex-start).
    // Carousel ke andar ek overflow-x:auto scroll container hota hai —
    // shrink-to-fit + scroll-container ka combination kabhi-kabhi WebView
    // mein bubble ko galat (bahut chhoti/asymmetric) width de deta hai,
    // jisse video off-center/cut dikhta hai. Bubble ko poori available
    // width tak explicitly stretch kar do taaki yeh ambiguity hi na ho.
    if (isCarousel) bubble.style.alignSelf = 'stretch';
    const grid = document.createElement('div');
    grid.className = isCarousel ? 'media-carousel-wrap' : 'media-grid';
    const track = isCarousel ? document.createElement('div') : null;
    if (track) track.className = 'media-carousel';

    function appendMedia(el) {
      if (isCarousel) {
        const slide = document.createElement('div');
        slide.className = 'media-slide';
        slide.appendChild(el);
        track.appendChild(slide);
      } else {
        grid.appendChild(el);
      }
    }

    mediaItems.forEach(item => {
      if (item.type === 'hls') {
        appendMedia(renderHlsPlayer(item));
      } else if (item.type === 'image') {

        // Wrapper — shimmer loading effect ke saath
        const imgWrap = document.createElement('div');
        imgWrap.style.cssText = 'width:100%;border-radius:14px;overflow:hidden;background:#1a1a1a;position:relative;min-height:200px;';

        // Shimmer animation
        const shimmer = document.createElement('div');
        shimmer.style.cssText = 'position:absolute;inset:0;background:linear-gradient(90deg,#1c1c1c 25%,#2a2a2a 50%,#1c1c1c 75%);background-size:200% 100%;animation:imgShimmer 1.4s infinite;';
        imgWrap.appendChild(shimmer);

        const img = document.createElement('img');
        img.alt = 'Image';
        img.style.cssText = 'width:100%;height:auto;display:block;border-radius:14px;opacity:0;transition:opacity 0.35s ease;';

        const isLocal = item.src.startsWith('/static/') || item.src.startsWith('./static/');
        // Pehle SEEDHA URL try karo (Render ka bandwidth bilkul use nahi
        // hota) — zyadatar images bina proxy ke hi load ho jaati hain,
        // kyunki <img> tag ko CORS ki zarurat nahi hoti (woh sirf canvas/
        // fetch ke liye matter karta hai). Sirf agar seedha load fail ho
        // (hotlink-protection wali kuch sites) tabhi proxy fallback.
        img.src = item.src;

        // Load ho to smoothly dikhao — full width, no tap needed
        img.onload = () => {
          shimmer.remove();
          imgWrap.style.minHeight = 'unset';
          img.style.opacity = '1';
        };

        // Fallback chain: direct -> proxy -> https -> error
        let stage = 0;
        img.onerror = () => {
          stage++;
          if (stage === 1 && !isLocal) {
            img.src = '/api/imgproxy?url=' + encodeURIComponent(item.src);
          } else if (stage === 2) {
            const httpsUrl = item.src.replace(/^http:\/\//, 'https://');
            if (httpsUrl !== item.src) { img.src = httpsUrl; return; }
            showErr();
          } else { showErr(); }
        };

        function showErr() {
          shimmer.remove();
          img.remove();
          imgWrap.style.cssText = 'width:100%;border-radius:14px;background:#111;padding:20px;display:flex;flex-direction:column;align-items:center;gap:8px;min-height:unset;';
          imgWrap.innerHTML = '<div style="font-size:36px">🖼️</div><div style="color:#555;font-size:12px;">Image load nahi hui</div>';
          const a = document.createElement('a');
          a.href = item.src; a.target = '_blank';
          a.style.cssText = 'font-size:12px;color:var(--accent);padding:5px 14px;border:1px solid var(--accent);border-radius:20px;text-decoration:none;';
          a.textContent = 'Browser mein kholkar dekho';
          imgWrap.appendChild(a);
        }

        imgWrap.appendChild(img);

        // ── Set as Background button ──
        const bgBtn = document.createElement('button');
        bgBtn.textContent = '🖼️ Chat background banao';
        bgBtn.style.cssText = 'position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,0.65);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:20px;padding:6px 12px;font-size:11px;cursor:pointer;backdrop-filter:blur(4px);opacity:0;transition:opacity 0.2s;z-index:5;';
        imgWrap.addEventListener('mouseenter', () => bgBtn.style.opacity = '1');
        imgWrap.addEventListener('mouseleave', () => bgBtn.style.opacity = '0');
        imgWrap.addEventListener('touchstart', () => bgBtn.style.opacity = '1');
        bgBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          setChatBackgroundImage(item.src, isLocal);
          setTimeout(openBgControls, 400);
        });
        imgWrap.appendChild(bgBtn);

        appendMedia(imgWrap);
      } else {
        const youtubeId = getYouTubeId(item.src);
        if (youtubeId) {
          // BUG FIX: yt-dlp direct-stream extraction and third-party
          // Invidious/Piped instances get blocked or go down often
          // (especially from cloud-hosted server IPs like Render) — that's
          // why videos had stopped playing in-chat and only a "open this
          // link" fallback was showing. The official YouTube embed iframe
          // does not depend on any scraping/extraction and reliably plays
          // inline in the chat bubble, so it's now used as the PRIMARY
          // method. yt-dlp is kept only as a background upgrade attempt.
          const wrapper = document.createElement('div');
          wrapper.className = 'yt-wrapper';
          wrapper.style.cssText = 'width:100%;max-width:340px;background:#000;border-radius:12px;overflow:hidden;border:1px solid var(--line);margin:4px 0;';

          if (item.title) {
            const titleBar = document.createElement('div');
            titleBar.style.cssText = 'background:#111;color:#fff;font-size:11px;padding:6px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
            titleBar.textContent = '▶ ' + item.title;
            wrapper.appendChild(titleBar);
          }

          // Primary: official YouTube embed — plays inline in chat immediately.
          const iframe = document.createElement('iframe');
          iframe.style.cssText = 'width:100%;aspect-ratio:16/9;border:none;display:block;background:#000;';
          iframe.src = `https://www.youtube-nocookie.com/embed/${youtubeId}?autoplay=0&rel=0&modestbranding=1`;
          iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen';
          iframe.allowFullscreen = true;
          iframe.loading = 'lazy';
          wrapper.appendChild(iframe);

          // Optional upgrade: agar yt-dlp se direct stream mil jaaye to
          // <video> tag mein switch kar do (no YouTube chrome/branding).
          // Chup-chaap background mein try hota hai — iframe hamesha turant
          // dikhta rehta hai isliye video kabhi "sirf link" nahi rahegi.
          const vid = document.createElement('video');
          vid.style.cssText = 'width:100%;aspect-ratio:16/9;display:none;background:#000;';
          vid.controls = true;
          vid.playsInline = true;
          vid.preload = 'none';
          wrapper.appendChild(vid);

          fetch('/api/ytstream?video_id=' + encodeURIComponent(youtubeId))
            .then(r => r.json())
            .then(data => {
              if (data.url) {
                vid.src = data.url;
                vid.onloadedmetadata = () => { iframe.style.display = 'none'; vid.style.display = 'block'; };
                vid.onerror = () => { /* iframe already showing — no-op */ };
              }
            })
            .catch(() => { /* iframe already showing — no-op */ });

          // Agar embedding hi block hai (kuch channels isse disable karte
          // hain), tabhi ek chhota "YouTube par kholo" link neeche dikhao —
          // video ab bhi bubble ke andar hi rehti hai, koi forced redirect nahi.
          const openLink = document.createElement('a');
          openLink.href = `https://www.youtube.com/watch?v=${youtubeId}`;
          openLink.target = '_blank';
          openLink.style.cssText = 'display:block;color:#888;font-size:10px;padding:5px 10px;text-align:right;background:#111;text-decoration:none;';
          openLink.textContent = 'YouTube par bhi khol sakte ho ↗';
          wrapper.appendChild(openLink);

          appendMedia(wrapper);
        } else {
          // General MP4 video — Pexels + Internet dono
          const wrapper = document.createElement('div');
          wrapper.style.cssText = 'width:100%;max-width:340px;border-radius:14px;overflow:hidden;border:1px solid var(--line);background:#000;margin:4px 0;';

          if (item.title) {
            const cap = document.createElement('div');
            cap.style.cssText = 'background:#111;color:#ccc;font-size:11px;padding:6px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
            cap.textContent = '🎬 ' + item.title;
            wrapper.appendChild(cap);
          }

          const vid = document.createElement('video');
          vid.style.cssText = 'width:100%;aspect-ratio:16/9;display:block;background:#000;';
          vid.controls = true;
          vid.preload = 'metadata';
          vid.playsInline = true;
          vid.autoplay = false;

          // Pehle SEEDHA URL try karo (Render bandwidth save) — video
          // playback ko bhi CORS ki zarurat nahi hoti jab tak canvas/
          // WebGL access na ho. Sirf direct fail hone par proxy fallback.
          vid.src = item.src;

          let triedProxy = false;
          vid.onerror = () => {
            if (!triedProxy) {
              triedProxy = true;
              // Direct fail (hotlink-protection) — proxy try karo
              vid.src = '/api/videoproxy?url=' + encodeURIComponent(item.src);
            } else {
              // Dono fail — thumbnail + open button dikhao
              vid.style.display = 'none';
              const btnDiv = document.createElement('div');
              btnDiv.style.cssText = 'padding:20px;text-align:center;background:#111;';
              btnDiv.innerHTML = '<div style="font-size:36px">🎬</div><div style="color:#aaa;font-size:12px;margin:6px 0">' + (item.title||'Video') + '</div>';
              const openBtn = document.createElement('a');
              openBtn.href = item.src;
              openBtn.target = '_blank';
              openBtn.style.cssText = 'display:inline-block;padding:8px 16px;background:var(--accent);color:#000;border-radius:20px;font-size:12px;font-weight:600;text-decoration:none;margin-top:8px;';
              openBtn.textContent = '▶ Browser mein kholkar dekho';
              btnDiv.appendChild(openBtn);
              wrapper.appendChild(btnDiv);
            }
          };

          wrapper.appendChild(vid);
          appendMedia(wrapper);
        }
      }
    });

    if (isCarousel) {
      grid.appendChild(track);

      // Prev/Next arrow buttons
      const prevBtn = document.createElement('button');
      prevBtn.className = 'carousel-arrow carousel-arrow-prev';
      prevBtn.innerHTML = '‹';
      prevBtn.setAttribute('aria-label', 'Peechhe');
      const nextBtn = document.createElement('button');
      nextBtn.className = 'carousel-arrow carousel-arrow-next';
      nextBtn.innerHTML = '›';
      nextBtn.setAttribute('aria-label', 'Aage');

      const currentIndex = () => Math.round(track.scrollLeft / track.clientWidth);

      const goToSlide = (idx) => {
        const clamped = Math.max(0, Math.min(mediaItems.length - 1, idx));
        track.scrollTo({ left: clamped * track.clientWidth, behavior: 'smooth' });
      };

      prevBtn.addEventListener('click', () => goToSlide(currentIndex() - 1));
      nextBtn.addEventListener('click', () => goToSlide(currentIndex() + 1));
      grid.appendChild(prevBtn);
      grid.appendChild(nextBtn);

      // Dot indicators — kitne items hain aur abhi kaunsa dikh raha hai
      const dots = document.createElement('div');
      dots.className = 'carousel-dots';
      const dotEls = mediaItems.map((_, i) => {
        const d = document.createElement('span');
        d.className = 'carousel-dot' + (i === 0 ? ' active' : '');
        d.addEventListener('click', () => goToSlide(i));
        dots.appendChild(d);
        return d;
      });
      grid.appendChild(dots);

      const countBadge = document.createElement('div');
      countBadge.className = 'carousel-count';
      countBadge.textContent = `1 / ${mediaItems.length}`;
      grid.appendChild(countBadge);

      // Android WebView mein CSS scroll-snap kabhi-kabhi properly kaam nahi
      // karta (video 2 slides ke beech "stuck"/off-center reh jaata hai).
      // Isliye scroll rukne ke baad JS se FORCE-SNAP karte hain — yeh
      // pakka guarantee karta hai ki hamesha ek hi slide poori tarah
      // center mein aake settle ho, chahe browser ka native snap kaam
      // kare ya na kare.
      let scrollTimer = null;
      track.addEventListener('scroll', () => {
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(() => {
          const idx = Math.max(0, Math.min(mediaItems.length - 1, currentIndex()));
          const target = idx * track.clientWidth;
          // Agar scroll thoda bhi off hai (in-between do slides ke), to
          // exact position par force-snap kar do.
          if (Math.abs(track.scrollLeft - target) > 2) {
            track.scrollTo({ left: target, behavior: 'smooth' });
          }
          dotEls.forEach((d, i) => d.classList.toggle('active', i === idx));
          countBadge.textContent = `${idx + 1} / ${mediaItems.length}`;
        }, 120);
      }, { passive: true });
    }

    bubble.appendChild(grid);
  }

  // HLS control commands (pause/resume/stop) — currently active player par apply karo
  if (hlsControls.length) {
    hlsControls.forEach(action => handleHlsControl(action));
  }

  // Radio — persistent bar mein load karo (inline player nahi)
  if (radioItems.length > 0) {
    // Chat mein sirf confirmation dikhao
    const radioNote = document.createElement('div');
    radioNote.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 12px;background:rgba(99,179,237,0.08);border:1px solid rgba(99,179,237,0.2);border-radius:12px;margin-top:8px;';
    radioNote.innerHTML = '<span style="font-size:20px">📻</span><div><div style="font-size:13px;font-weight:600;color:#63b3ed;">' + radioItems[0].name + '</div><div style="font-size:11px;color:#4a5568;">' + radioItems.length + ' station' + (radioItems.length > 1 ? 's' : '') + ' mili — player mein chal rahi hai</div></div>';
    bubble.appendChild(radioNote);
    // Persistent radio bar mein load karo
    window.radioPlayer.loadPlaylist(radioItems);
  }

  // FILE download buttons dikhao
  if (fileBlocks.length > 0) {
    const fileDiv = document.createElement('div');
    fileDiv.style.cssText = 'margin-top:10px;display:flex;flex-direction:column;gap:8px;';
    fileBlocks.forEach(fb => {
      const btn = document.createElement('button');
      btn.style.cssText = 'display:flex;align-items:center;gap:8px;background:var(--accent);color:#000;border:none;border-radius:10px;padding:10px 14px;cursor:pointer;font-size:13px;font-weight:600;width:100%;text-align:left;';
      btn.innerHTML = '<span style="font-size:18px">⬇️</span><span>' + fb.filename + '</span>';
      btn.onclick = async () => {
        btn.disabled = true;
        btn.style.opacity = '0.7';
        try {
          const res = await fetch('/api/create_file', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ filename: fb.filename, content: fb.content })
          });
          const d = await res.json();
          if (d.warning) {
            showBgToast('⚠️ ' + d.warning);
          }
          const a = document.createElement('a');
          a.href = '/api/download/' + d.token;
          a.download = fb.filename;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        } catch(e) { alert('Download mein error: ' + e); }
        btn.disabled = false;
        btn.style.opacity = '1';
      };
      fileDiv.appendChild(btn);
    });
    bubble.appendChild(fileDiv);
  }

  // ZIP project download button
  if (showZipDownload) {
    const zipBtn = document.createElement('a');
    zipBtn.className = 'zip-download-btn';
    zipBtn.href = '/api/download_project_zip';
    zipBtn.download = '';
    zipBtn.innerHTML = '📦 Updated Jarvis ZIP Download karo';
    bubble.appendChild(zipBtn);
  }

  if (!workingText && !mediaItems.length && !fileBlocks.length && !(attachments && attachments.length)) bubble.textContent = text;
  msg.appendChild(label); msg.appendChild(bubble);

  // Copy button — Jarvis messages ke neeche
  if (who === 'jarvis') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-copy-btn';
    copyBtn.textContent = '📋 Copy';
    copyBtn.onclick = () => {
      navigator.clipboard.writeText(bubble.innerText || bubble.textContent).then(() => {
        copyBtn.textContent = '✅ Copied'; setTimeout(() => copyBtn.textContent = '📋 Copy', 1500);
      });
    };
    actions.appendChild(copyBtn);
    msg.appendChild(actions);
  }

  chat.appendChild(msg);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function addTypingIndicator() {
  const msg = document.createElement('div'); msg.className = 'msg jarvis'; msg.id = 'typingMsg';
  const label = document.createElement('div'); label.className = 'msg-label'; label.textContent = 'JARVIS';

  const bubble = document.createElement('div'); bubble.className = 'msg-bubble typing-claude';
  bubble.innerHTML = '<div class="thinking-dots"><span></span><span></span><span></span></div><span class="thinking-text">Soch raha hoon</span>';
  msg.appendChild(label); msg.appendChild(bubble); chat.appendChild(msg); chat.scrollTop = chat.scrollHeight;
}
function removeTypingIndicator() { const el = document.getElementById('typingMsg'); if (el) el.remove(); }
function setCoreState(state, statusText) { if(window.core) core.className = `core ${state}`; }

// BUG FIX (v11.1): chat.innerHTML = '' seedha DOM se <video>+hls.js wale
// nodes hata deta tha bina unka hls.destroy() call kiye — orphaned Hls.js
// instance background mein segments fetch/buffer karta rehta tha (network +
// memory leak), khaaskar jab user stream chalte hue hi chat switch/naya
// chat banata tha. Ab har jagah chat.innerHTML clear karne se PEHLE saare
// active HLS players explicitly stop/destroy karte hain.
function _cleanupActiveHlsPlayers() {
  if (!window._jarvisHlsPlayers) return;
  [...window._jarvisHlsPlayers].forEach(entry => { try { _stopHlsEntry(entry); } catch (e) {} });
}

function showWelcome() { _cleanupActiveHlsPlayers(); chat.innerHTML = ''; addMessage('Namaste. Main Jarvis hoon, aapki seva mein hazir. Photo ya PDF bhi bhej sakte ho!', 'jarvis'); }

// ---------- Chat management ----------
// Advanced: har step try/catch mein hai taaki koi bhi ek error (network/parsing/
// corrupt history) poori chat UI ko permanently freeze na kar sake — kam se kam
// composer hamesha usable rahega (fallback: ek fresh chat ban jayegi).
async function initChats() {
  try {
    const res = await fetch('/api/chats');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.chats && data.chats.length > 0) {
      currentChatId = data.chats[0].id;
      await loadChatMessages(currentChatId);
    } else {
      await startNewChat();
    }
    renderChatList(data.chats || []);
  } catch (err) {
    console.error('initChats failed:', err);
    // Fallback: purani history load na ho paaye to bhi ek fresh, kaam karti hui
    // chat bana do — taaki user kabhi bhi "frozen/blank" screen na dekhe.
    try {
      await startNewChat();
    } catch (err2) {
      console.error('startNewChat fallback bhi fail hua:', err2);
      // Aakhri fallback: server bilkul unreachable hai. Local-only ek chhota
      // sa temp ID de do taaki composer kam se kam disabled na dikhe, aur
      // user ko clearly bata do ki kya problem hai.
      currentChatId = 'offline_' + Date.now();
      _cleanupActiveHlsPlayers();
      chat.innerHTML = '';
      addMessage('⚠️ Server se connect nahi ho paya. Termux mein "python server.py" chal raha hai check karo, phir page reload karo.', 'jarvis');
    }
  }
}
async function startNewChat() {
  const res = await fetch('/api/chats/new', { method: 'POST' });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  currentChatId = data.chat_id;
  showWelcome();
  try { await refreshChatList(); } catch (e) { console.error('refreshChatList fail:', e); }
  closeSidebar();
}
async function loadChatMessages(chatId) {
  currentChatId = chatId;
  try {
    const res = await fetch(`/api/chats/${chatId}`);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    _cleanupActiveHlsPlayers();
    chat.innerHTML = '';
    if (data.messages && data.messages.length > 0) {
      data.messages.forEach(m => {
        // Har message alag try/catch mein — ek corrupt/purana message poori
        // history ka render kabhi na roke.
        try { addMessage(m.content, m.role === 'user' ? 'user' : 'jarvis'); }
        catch (e) { console.error('Ek message render nahi ho paya, skip:', e); }
      });
    } else {
      showWelcome();
    }
  } catch (err) {
    console.error('loadChatMessages fail:', err);
    showWelcome();
  }
}
async function refreshChatList() {
  try {
    const res = await fetch('/api/chats');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    renderChatList(data.chats || []);
  } catch (err) {
    console.error('refreshChatList fail:', err);
  }
}
function renderChatList(chats) {
  chatListEl.innerHTML = '';
  if (!chats.length) { chatListEl.innerHTML = '<div class="chat-empty">Koi baat-cheet nahi hai abhi.</div>'; return; }
  chats.forEach(c => {
    const item = document.createElement('div'); item.className = 'chat-item' + (c.id === currentChatId ? ' active' : '');
    const title = document.createElement('div'); title.className = 'chat-item-title'; title.textContent = c.title || 'Nayi Baat-cheet';
    title.addEventListener('click', async () => { await loadChatMessages(c.id); renderChatList(chats.map(x=>({...x}))); closeSidebar(); });
    const delBtn = document.createElement('button'); delBtn.className = 'chat-item-delete';
    delBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
    delBtn.addEventListener('click', async e => { e.stopPropagation(); await fetch(`/api/chats/${c.id}`, { method: 'DELETE' }); if (c.id === currentChatId) await initChats(); else await refreshChatList(); });
    item.appendChild(title); item.appendChild(delBtn); chatListEl.appendChild(item);
  });
}
function openSidebar() { sidebarOverlay.classList.add('open'); refreshChatList(); }
function closeSidebar() { sidebarOverlay.classList.remove('open'); }
menuBtn.addEventListener('click', openSidebar);
sidebarCloseBtn.addEventListener('click', closeSidebar);

sidebarOverlay.addEventListener('click', e => { if (e.target === sidebarOverlay) closeSidebar(); });
newChatBtn.addEventListener('click', startNewChat);

// ---------- File upload ----------
const attachBtn = document.getElementById('attachBtn');
const fileInput = document.getElementById('fileInput');
const pendingPreview = document.getElementById('pendingPreview');

function fileToBase64(file) {
  return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file); });
}
function getFileType(file) {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type === 'application/pdf') return 'pdf';
  if (file.type.startsWith('video/')) return 'video';
  if (file.type.startsWith('audio/')) return 'audio';
  if (file.type.includes('zip') || file.name.endsWith('.zip')) return 'zip';
  if (file.type.includes('text') || file.name.endsWith('.txt')) return 'text';
  if (file.name.endsWith('.docx') || file.name.endsWith('.doc')) return 'doc';
  if (file.name.endsWith('.xlsx') || file.name.endsWith('.csv')) return 'spreadsheet';
  if (file.name.endsWith('.json')) return 'json';
  if (file.name.endsWith('.py') || file.name.endsWith('.js') || file.name.endsWith('.html')) return 'code';
  return 'file';
}

function getFileIcon(type, name) {
  const icons = {
    'pdf': '📄', 'video': '🎬', 'audio': '🎵', 'zip': '🗜️',
    'text': '📝', 'doc': '📝', 'spreadsheet': '📊', 'json': '📋',
    'code': '💻', 'file': '📎'
  };
  return icons[type] || '📎';
}
async function handleFiles(files) {
  for (const file of files) {
    try {
      const base64 = await fileToBase64(file);
      pendingFiles.push({ file, base64, type: getFileType(file), name: file.name });
    } catch (err) {
      console.error('File read failed:', file.name, err);
      alert('⚠️ "' + file.name + '" load nahi ho payi: ' + (err && err.message ? err.message : err));
    }
  }
  renderPendingPreview();
}
function renderPendingPreview() {
  pendingPreview.innerHTML = '';
  if (!pendingFiles.length) { pendingPreview.classList.add('hidden'); return; }
  pendingPreview.classList.remove('hidden');
  pendingFiles.forEach((f, i) => {
    const item = document.createElement('div'); item.className = 'pending-item';
    if (f.type === 'image') { const img = document.createElement('img'); img.src = f.base64; img.className = 'pending-thumb'; item.appendChild(img); }
    else { const icon = document.createElement('span'); icon.textContent = getFileIcon(f.type, f.name); icon.className = 'pending-icon'; item.appendChild(icon); }
    const name = document.createElement('span'); name.className = 'pending-name'; name.textContent = f.name;
    const del = document.createElement('button'); del.className = 'pending-del'; del.textContent = '✕';
    del.addEventListener('click', () => { pendingFiles.splice(i, 1); renderPendingPreview(); });
    item.appendChild(name); item.appendChild(del); pendingPreview.appendChild(item);
  });
}
attachBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', e => { if (e.target.files.length) handleFiles(Array.from(e.target.files)); fileInput.value = ''; });
chat.addEventListener('dragover', e => { e.preventDefault(); chat.classList.add('drag-over'); });
chat.addEventListener('dragleave', () => chat.classList.remove('drag-over'));
chat.addEventListener('drop', e => { e.preventDefault(); chat.classList.remove('drag-over'); if (e.dataTransfer.files.length) handleFiles(Array.from(e.dataTransfer.files)); });

// ---------- Send message ----------
let _activeRequestController = null;

function _setSendingUI(isSending) {
  if (isSending) {
    sendBtn.classList.add('sending');
    sendBtn.setAttribute('aria-label', 'Rokho');
    sendBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><rect x="6" y="6" width="12" height="12" fill="currentColor" rx="2"/></svg>';
  } else {
    sendBtn.classList.remove('sending');
    sendBtn.setAttribute('aria-label', 'Bhejo');
    sendBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M3 11l18-8-8 18-2.5-7.5L3 11z"/></svg>';
  }
}

async function sendMessage(text) {
  if (!text.trim() && !pendingFiles.length) return;
  if (!currentChatId) {
    // Pehle silently kuch nahi hota tha — ab auto-recover: ek naya chat
    // bana ke turant retry karte hain, taaki composer kabhi "dead" na lage.
    try { await startNewChat(); } catch (e) { console.error(e); }
    if (!currentChatId) {
      addMessage('⚠️ Chat shuru nahi ho payi — server chal raha hai check karo, phir page reload karo.', 'jarvis');
      return;
    }
  }
  const filesToSend = [...pendingFiles]; pendingFiles = []; renderPendingPreview();
  const displayText = text.trim() || `[${filesToSend.map(f=>f.name).join(', ')}]`;
  addMessage(displayText, 'user', filesToSend);
  textInput.value = '';
  _resizeTextInput();
  setCoreState('thinking', 'Soch raha hoon…'); addTypingIndicator();

  _activeRequestController = new AbortController();
  _setSendingUI(true);

  try {
    const res = await fetch(`/api/chats/${currentChatId}/message`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text.trim(), files: filesToSend.map(f => ({ name: f.name, type: f.type, base64: f.base64 })) }),
      signal: _activeRequestController.signal,
    });
    let data;
    try {
      data = await res.json();
    } catch (jsonErr) {
      removeTypingIndicator();
      addMessage('Server ne JSON nahi bheja (HTTP ' + res.status + ') — Termux mein server.py ka error dekho.', 'jarvis');
      setCoreState('idle', 'Taiyaar hoon');
      return;
    }
    if (!res.ok || data.error) {
      removeTypingIndicator();
      addMessage('Server error ' + res.status + ': ' + (data.error || data.reply || JSON.stringify(data)), 'jarvis');
      setCoreState('idle', 'Taiyaar hoon');
      return;
    }
    removeTypingIndicator(); addMessage(data.reply, 'jarvis');
    speak(data.reply.replace(/IMAGE_GENERATED:[^\s]+/g,'').replace(/IMAGE_FOUND:\S+/g,'').replace(/VIDEO_FOUND:\S+?\|[^\n]*/g,'').replace(/HLS_FOUND:\S+?\|[^\n]*/g,'').replace(/HLS_CONTROL:\S+/g,'').trim());
    refreshChatList(); setCoreState('idle', 'Taiyaar hoon');
  } catch (err) {
    removeTypingIndicator();
    if (err.name === 'AbortError') {
      addMessage('⏹️ Roka gaya.', 'jarvis');
    } else {
      addMessage('Connection error: ' + (err.message || err) + ' — Server chal raha hai? (python server.py)', 'jarvis');
    }
    setCoreState('idle', 'Taiyaar hoon');
  } finally {
    _activeRequestController = null;
    _setSendingUI(false);
  }
}
sendBtn.addEventListener('click', () => {
  if (_activeRequestController) {
    _activeRequestController.abort();
    return;
  }
  sendMessage(textInput.value);
});
textInput.addEventListener('keydown', e => {
  // Pehle plain Enter bhi turant message send kar deta tha, jisse
  // textarea mein newline (nayi line) kabhi type hi nahi ho pata tha.
  // Ab: plain Enter => normal newline (default textarea behavior).
  //     Ctrl+Enter ya Cmd+Enter => message send.
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendMessage(textInput.value);
  }
});

// ---------- Auto-growing textarea (Claude-style, upar ki taraf badhta hai) ----------
function _resizeTextInput() {
  textInput.style.height = 'auto';
  const maxHeight = 160;
  const newHeight = Math.min(textInput.scrollHeight, maxHeight);
  textInput.style.height = newHeight + 'px';
  textInput.style.overflowY = textInput.scrollHeight > maxHeight ? 'auto' : 'hidden';
}
textInput.addEventListener('input', _resizeTextInput);
_resizeTextInput();

// ---------- Voice input ----------
let recognition = null; let isListening = false;
function initSpeechRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition; if (!SR) return null;
  const r = new SR(); r.lang = 'hi-IN'; r.continuous = false; r.interimResults = false;
  r.onstart = () => { isListening = true; micBtn.classList.add('active'); setCoreState('listening', 'Sun raha hoon…'); };
  r.onresult = e => sendMessage(e.results[0][0].transcript);
  r.onerror = () => setCoreState('idle', 'Taiyaar hoon');
  r.onend = () => { isListening = false; micBtn.classList.remove('active'); setCoreState('idle', 'Taiyaar hoon'); };
  return r;
}
recognition = initSpeechRecognition();
micBtn.addEventListener('click', () => { if (!recognition) { addMessage('Voice support nahi hai. Chrome try karo.', 'jarvis'); return; } if (isListening) recognition.stop(); else recognition.start(); });

// ---------- Voice output ----------
let availableVoices = []; let selectedVoiceURI = localStorage.getItem('jarvisVoiceURI') || null;
let voiceGender = localStorage.getItem('jarvisVoiceGender') || 'male';
let voiceMode = localStorage.getItem('jarvisVoiceMode') || 'natural';
function loadVoices() { availableVoices = window.speechSynthesis ? window.speechSynthesis.getVoices() : []; populateVoiceDropdown(); }
if (window.speechSynthesis) { window.speechSynthesis.onvoiceschanged = loadVoices; loadVoices(); }
function getHindiVoices() { return availableVoices.filter(v => v.lang && v.lang.toLowerCase().startsWith('hi')); }
const FEMALE_HINTS = ['female','woman','swara','priya','heera','lekha'];
const MALE_HINTS = ['male','man','madhur','prabhat','hemant','ravi'];
function guessGender(v) { const n = v.name.toLowerCase(); if (FEMALE_HINTS.some(h=>n.includes(h))) return 'female'; if (MALE_HINTS.some(h=>n.includes(h))) return 'male'; return 'unknown'; }
function pickVoice(gender) { const hv = getHindiVoices(); if (!hv.length) return null; if (selectedVoiceURI) { const e = hv.find(v=>v.voiceURI===selectedVoiceURI); if (e) return e; } return hv.find(v=>guessGender(v)===gender) || hv[0]; }
function speakFast(text) {
  if (!window.speechSynthesis) return; window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = 'hi-IN'; u.rate = 1.0;
  const v = pickVoice(voiceGender); if (v) u.voice = v;
  u.onstart = () => setCoreState('speaking', 'Bol raha hoon…'); u.onend = () => setCoreState('idle', 'Taiyaar hoon');
  window.speechSynthesis.speak(u);
}
let currentAudio = null;
async function speakNatural(text) {
  setCoreState('thinking', 'Awaaz taiyaar kar raha hoon…');
  try {
    const res = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, gender: voiceGender }) });
    if (!res.ok) throw new Error('TTS failed');
    const blob = await res.blob(); const url = URL.createObjectURL(blob);
    if (currentAudio) currentAudio.pause();
    currentAudio = new Audio(url);
    currentAudio.onplay = () => setCoreState('speaking', 'Bol raha hoon…');
    currentAudio.onended = () => { setCoreState('idle', 'Taiyaar hoon'); URL.revokeObjectURL(url); };
    currentAudio.onerror = () => setCoreState('idle', 'Taiyaar hoon');
    await currentAudio.play();
  } catch { speakFast(text); }
}
function speak(text) { if (voiceMode === 'natural') speakNatural(text); else speakFast(text); }
function populateVoiceDropdown() {
  const sel = document.getElementById('voiceSelect'); if (!sel) return;
  const hv = getHindiVoices(); sel.innerHTML = '';
  if (!hv.length) { const o = document.createElement('option'); o.textContent = 'Koi Hindi awaaz nahi mili'; sel.appendChild(o); return; }
  hv.forEach(v => { const o = document.createElement('option'); o.value = v.voiceURI; const g = guessGender(v); o.textContent = v.name + (g==='female'?' (ladki)':g==='male'?' (ladka)':''); if (v.voiceURI===selectedVoiceURI) o.selected=true; sel.appendChild(o); });
}
function setupSpeedControls() {
  const fb = document.getElementById('voiceFastBtn'); const nb = document.getElementById('voiceNaturalBtn');
  const upd = () => { if (fb) fb.classList.toggle('active', voiceMode==='fast'); if (nb) nb.classList.toggle('active', voiceMode==='natural'); };
  upd();
  if (fb) fb.addEventListener('click', () => { voiceMode='fast'; localStorage.setItem('jarvisVoiceMode', voiceMode); upd(); speak('Ab main turant bolunga.'); });
  if (nb) nb.addEventListener('click', () => { voiceMode='natural'; localStorage.setItem('jarvisVoiceMode', voiceMode); upd(); speak('Ab main behtar awaaz mein bolunga.'); });
}
setupSpeedControls();
function setupVoiceControls() {
  const mb = document.getElementById('voiceMaleBtn'); const fb = document.getElementById('voiceFemaleBtn'); const sel = document.getElementById('voiceSelect');
  const upd = () => { if (mb) mb.classList.toggle('active', voiceGender==='male'); if (fb) fb.classList.toggle('active', voiceGender==='female'); }; upd();
  if (mb) mb.addEventListener('click', () => { voiceGender='male'; selectedVoiceURI=null; localStorage.setItem('jarvisVoiceGender', voiceGender); localStorage.removeItem('jarvisVoiceURI'); upd(); populateVoiceDropdown(); speak('Ab main is awaaz mein bolunga.'); });
  if (fb) fb.addEventListener('click', () => { voiceGender='female'; selectedVoiceURI=null; localStorage.setItem('jarvisVoiceGender', voiceGender); localStorage.removeItem('jarvisVoiceURI'); upd(); populateVoiceDropdown(); speak('Ab main is awaaz mein bolungi.'); });
  if (sel) sel.addEventListener('change', () => { selectedVoiceURI=sel.value; localStorage.setItem('jarvisVoiceURI', selectedVoiceURI); speak('Yeh meri nayi awaaz hai.'); });
}
setupVoiceControls();

// ---------- Voice Preview Button ----------
const voicePreviewBtn = document.getElementById('voicePreviewBtn');
if (voicePreviewBtn) {
  voicePreviewBtn.addEventListener('click', () => {
    const sampleLines = [
      'Namaste! Main Jarvis hoon, aapki madad ke liye taiyaar hoon.',
      'Yeh meri awaaz hai, kaisi lag rahi hai?',
      'Bataiye, aaj main aapki kya madad kar sakta hoon.'
    ];
    const line = sampleLines[Math.floor(Math.random() * sampleLines.length)];
    speak(line);
  });
}

// ---------- Settings ----------
settingsBtn.addEventListener('click', async () => { sheetOverlay.classList.add('open'); await loadKeys(); });
sheetClose.addEventListener('click', () => sheetOverlay.classList.remove('open'));
sheetOverlay.addEventListener('click', e => { if (e.target===sheetOverlay) sheetOverlay.classList.remove('open'); });
async function loadKeys() {
  try { const res = await fetch('/api/keys'); const data = await res.json(); keyList.innerHTML = '';
    if (!data.keys || !data.keys.length) { keyList.innerHTML = '<span class="key-empty">Abhi koi key save nahi hai.</span>'; return; }
    data.keys.forEach(k => { const chip = document.createElement('span'); chip.className = 'key-chip'; chip.textContent = k; keyList.appendChild(chip); });
  } catch { keyList.innerHTML = '<span class="key-empty">Keys load nahi ho payi.</span>'; }
}
keyForm.addEventListener('submit', async e => {
  e.preventDefault();
  const name = document.getElementById('keyName').value.trim(); const value = document.getElementById('keyValue').value.trim();
  if (!name || !value) return;
  await fetch('/api/save_key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, value }) });
  document.getElementById('keyName').value = ''; document.getElementById('keyValue').value = ''; await loadKeys();
});
const keyFormSubmitBtn = document.getElementById('keyFormSubmit');
if (keyFormSubmitBtn) {
  keyFormSubmitBtn.addEventListener('click', async () => {
    const name = document.getElementById('keyName').value.trim();
    const value = document.getElementById('keyValue').value.trim();
    if (!name || !value) { alert('Naam aur key dono bharo!'); return; }
    keyFormSubmitBtn.textContent = 'Saving...';
    keyFormSubmitBtn.disabled = true;
    try {
      const res = await fetch('/api/save_key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, value })
      });
      const data = await res.json();
      document.getElementById('keyName').value = '';
      document.getElementById('keyValue').value = '';
      await loadKeys();
      keyFormSubmitBtn.textContent = '✅ Yaad rakhi!';
      setTimeout(() => { keyFormSubmitBtn.textContent = 'Yaad rakho'; keyFormSubmitBtn.disabled = false; }, 1500);
    } catch (err) {
      keyFormSubmitBtn.textContent = '❌ Error!';
      setTimeout(() => { keyFormSubmitBtn.textContent = 'Yaad rakho'; keyFormSubmitBtn.disabled = false; }, 1500);
    }
  });
}

// ---------- Model picker ----------
const modelBar = document.getElementById('modelBar'); const modelBarLabel = document.getElementById('modelBarLabel');
const modelSheetOverlay = document.getElementById('modelSheetOverlay'); const modelSheetClose = document.getElementById('modelSheetClose');
const modelListEl = document.getElementById('modelList');
async function loadModelBarLabel() {
  try { const res = await fetch('/api/models'); const data = await res.json(); const cur = (data.models||[]).find(m=>m.id===data.selected); modelBarLabel.textContent = cur ? cur.label : 'Auto'; }
  catch { modelBarLabel.textContent = 'Auto'; }
}
async function openModelSheet() {
  modelSheetOverlay.classList.add('open');
  try { const res = await fetch('/api/models'); const data = await res.json(); renderModelList(data.models||[], data.selected); }
  catch { modelListEl.innerHTML = '<span class="key-empty">Models load nahi ho paye.</span>'; }
}
function renderModelList(models, selected) {
  modelListEl.innerHTML = '';
  const groups = {}; models.forEach(m => { const g = m.group||''; if (!groups[g]) groups[g]=[]; groups[g].push(m); });
  ['','Groq','Gemini','OpenRouter'].forEach(gName => {
    const items = groups[gName]; if (!items||!items.length) return;
    if (gName) { const h = document.createElement('div'); h.className='model-group-header'; h.textContent=gName; modelListEl.appendChild(h); }
    items.forEach(m => {
      const item = document.createElement('div'); item.className = 'model-item'+(m.id===selected?' active':'');
      const lbl = document.createElement('span'); lbl.className='model-item-label'; lbl.textContent=m.label;
      const chk = document.createElement('span'); chk.className='model-item-check'; chk.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>';
      item.appendChild(lbl); item.appendChild(chk);
      item.addEventListener('click', async () => { await fetch('/api/models/select', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model_id:m.id}) }); modelBarLabel.textContent=m.label; modelSheetOverlay.classList.remove('open'); });
      modelListEl.appendChild(item);
    });
  });
}
modelBar.addEventListener('click', openModelSheet);
modelSheetClose.addEventListener('click', () => modelSheetOverlay.classList.remove('open'));
modelSheetOverlay.addEventListener('click', e => { if (e.target===modelSheetOverlay) modelSheetOverlay.classList.remove('open'); });
loadModelBarLabel();

// ---------- Tab switching ----------
const tabChat = document.getElementById('tabChat');
const navChat = document.getElementById('navChat');
const topbarSub = document.getElementById('topbarSub');
function switchTab(tab) {
  // Sirf chat tab hai ab — search tab hata diya gaya hai
  tabChat.classList.remove('hidden');
  navChat.classList.add('active');
  topbarSub.textContent = 'v1 · personal core';
}
navChat.addEventListener('click', () => switchTab('chat'));
// navSearch — search tab hata diya gaya hai

// ============================================================
// ---------- Init ----------
initChats();
// Note: service worker registration index.html mein hoti hai (/service-worker.js).
// Yahan pehle ek DUPLICATE registration thi (/static/sw.js — jo exist hi nahi
// karti thi, isliye hamesha 404 deti thi). Woh hata di gayi hai.

// Search context - removed (search tab hata diya gaya hai)

// ═══════════════════════════════════════════════════
// PERSISTENT RADIO PLAYER
// ═══════════════════════════════════════════════════
window.radioPlayer = (() => {
  const bar      = document.getElementById('radioBar');
  const audio    = document.getElementById('radioAudio');
  const nameEl   = document.getElementById('radioStationName');
  const statusEl = document.getElementById('radioStatus');
  const playBtn  = document.getElementById('radioPlayBtn');
  const prevBtn  = document.getElementById('radioPrevBtn');
  const nextBtn  = document.getElementById('radioNextBtn');
  const closeBtn = document.getElementById('radioCloseBtn');
  const volSlider= document.getElementById('radioVolume');
  const chat     = document.getElementById('chat');

  let playlist = [];   // [{url, name}, ...]
  let current  = 0;
  let isOpen   = false;

  // ── Helpers ──────────────────────────────────────
  function setStatus(msg, type) {
    statusEl.textContent = msg;
    bar.classList.remove('playing', 'error');
    if (type) bar.classList.add(type);
  }

  function openBar() {
    if (!isOpen) {
      bar.classList.remove('hidden');
      // visible class — CSS max-height transition trigger karega
      requestAnimationFrame(() => {
        requestAnimationFrame(() => bar.classList.add('visible'));
      });
      isOpen = true;
    }
  }

  function closeBar() {
    bar.classList.remove('visible');
    // max-height transition ke baad hidden karo
    setTimeout(() => { bar.classList.add('hidden'); }, 300);
    audio.pause();
    audio.src = '';
    isOpen = false;
    playlist = [];
    current = 0;
  }

  // ── Load a station by index ───────────────────────
  function playStation(idx) {
    if (!playlist.length) return;
    current = ((idx % playlist.length) + playlist.length) % playlist.length;
    const st = playlist[current];

    nameEl.textContent = '\u{1F4FB} ' + st.name;
    setStatus('Connecting...', '');
    playBtn.textContent = '\u23F8';

    audio.pause();
    audio.src = '';

    // Pehle SEEDHA URL try karo (Render ka bandwidth use nahi hota) —
    // audio playback ko bhi CORS ki zarurat nahi hoti jab tak raw audio
    // data (Web Audio API) na padhna ho, jo yahan nahi ho raha. Sirf
    // agar direct load fail ho tabhi proxy fallback (neeche error
    // handler mein). Radio ghanton chalta hai, isliye yeh farak
    // bandwidth mein bahut bada pad sakta hai.
    audio.src = st.url;
    audio.load();

    audio.play().catch(() => {
      // Autoplay block — user ko play karna hoga
      setStatus('Play dabao sunne ke liye', '');
      playBtn.textContent = '\u25B6';
    });
  }

  // ── Load full playlist ────────────────────────────
  function loadPlaylist(stations) {
    playlist = stations;
    current = 0;
    openBar();
    playStation(0);
  }

  // ── Audio events ──────────────────────────────────
  audio.addEventListener('playing', () => {
    setStatus('\u25CF LIVE \u2014 ' + (playlist[current] || {}).name, 'playing');
    playBtn.textContent = '\u23F8';
  });

  audio.addEventListener('waiting', () => setStatus('Buffering...', ''));
  audio.addEventListener('stalled', () => setStatus('Stream stalled...', ''));

  audio.addEventListener('error', () => {
    const st = playlist[current];
    if (!st) return;
    // Direct URL fail ho gaya (CORS/hotlink block) — ab proxy try karo
    if (!audio.src.includes('/api/radiostream')) {
      setStatus('Retrying...', '');
      audio.src = '/api/radiostream?url=' + encodeURIComponent(st.url);
      audio.load();
      audio.play().catch(() => setStatus('Play dabao', ''));
    } else {
      setStatus('\u26A0 Stream nahi mila — next try karo', 'error');
    }
  });

  audio.addEventListener('pause', () => {
    playBtn.textContent = '\u25B6';
    bar.classList.remove('playing');
  });

  // ── Volume ────────────────────────────────────────
  audio.volume = parseFloat(volSlider.value);
  volSlider.addEventListener('input', () => {
    audio.volume = parseFloat(volSlider.value);
  });

  // ── Buttons ───────────────────────────────────────
  playBtn.addEventListener('click', () => {
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });

  prevBtn.addEventListener('click', () => playStation(current - 1));
  nextBtn.addEventListener('click', () => playStation(current + 1));
  closeBtn.addEventListener('click', closeBar);

  // Public API
  return { loadPlaylist, closeBar, playStation };
})();

// ═══════════════════════════════════════════════════
// AI-POWERED THEME SYSTEM
// ═══════════════════════════════════════════════════
const JARVIS_THEMES = {
  default: {
    label: "🤖 Jarvis Default",
    vars: {
      "--bg": "#0a0a0f", "--surface": "#111118", "--line": "#1e1e2e",
      "--text": "#e0e0f0", "--text-dim": "#666680", "--accent": "#00d4ff",
      "--accent2": "#7b5ea7", "--msg-user": "#1a1a2e", "--msg-jarvis": "#0d0d1a"
    }
  },
  ironman: {
    label: "🔴 Iron Man",
    vars: {
      "--bg": "#0d0000", "--surface": "#1a0505", "--line": "#3d1010",
      "--text": "#ffd0d0", "--text-dim": "#804040", "--accent": "#ff4444",
      "--accent2": "#ffaa00", "--msg-user": "#1f0a0a", "--msg-jarvis": "#0f0505"
    }
  },
  matrix: {
    label: "🟢 Matrix",
    vars: {
      "--bg": "#000500", "--surface": "#001200", "--line": "#003300",
      "--text": "#00ff41", "--text-dim": "#006600", "--accent": "#00ff41",
      "--accent2": "#00aa2a", "--msg-user": "#001500", "--msg-jarvis": "#000a00"
    }
  },
  ocean: {
    label: "🌊 Deep Ocean",
    vars: {
      "--bg": "#000d1a", "--surface": "#001829", "--line": "#002d4d",
      "--text": "#b0e0ff", "--text-dim": "#406080", "--accent": "#00b4ff",
      "--accent2": "#0066cc", "--msg-user": "#001525", "--msg-jarvis": "#000d1a"
    }
  },
  purple: {
    label: "💜 Purple Galaxy",
    vars: {
      "--bg": "#080010", "--surface": "#12001e", "--line": "#2d0050",
      "--text": "#e8c0ff", "--text-dim": "#6030a0", "--accent": "#cc44ff",
      "--accent2": "#7700cc", "--msg-user": "#150020", "--msg-jarvis": "#08000f"
    }
  },
  gold: {
    label: "✨ Gold",
    vars: {
      "--bg": "#0a0800", "--surface": "#1a1400", "--line": "#3d3000",
      "--text": "#ffe080", "--text-dim": "#806000", "--accent": "#ffd700",
      "--accent2": "#cc9900", "--msg-user": "#1a1200", "--msg-jarvis": "#0d0a00"
    }
  },
  white: {
    label: "☀️ Light Mode",
    vars: {
      "--bg": "#f5f5f5", "--surface": "#ffffff", "--line": "#e0e0e0",
      "--text": "#111111", "--text-dim": "#888888", "--accent": "#0066cc",
      "--accent2": "#7b5ea7", "--msg-user": "#e8f0fe", "--msg-jarvis": "#f8f8f8"
    }
  },
};

let _currentTheme = localStorage.getItem('jarvisTheme') || 'default';
let _aiThemeActive = false;

function applyTheme(themeKey, customVars) {
  const root = document.documentElement;
  let vars;
  if (customVars) {
    vars = customVars;
  } else {
    const t = JARVIS_THEMES[themeKey];
    if (!t) return;
    vars = t.vars;
  }
  Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
  _currentTheme = themeKey;
  localStorage.setItem('jarvisTheme', themeKey);
  if (!customVars) localStorage.removeItem('jarvisCustomTheme');
}

// Startup mein saved theme apply karo
(function initTheme() {
  const savedCustom = localStorage.getItem('jarvisCustomTheme');
  if (savedCustom) {
    try { applyTheme('custom', JSON.parse(savedCustom)); return; } catch(e) {}
  }
  applyTheme(_currentTheme);
})();

// ── Theme Panel UI ─────────────────────────────────
function openThemePanel() {
  // Existing panel remove karo
  const existing = document.getElementById('themePanel');
  if (existing) { existing.remove(); return; }

  const panel = document.createElement('div');
  panel.id = 'themePanel';
  panel.style.cssText = `
    position:fixed; bottom:80px; right:16px; z-index:9999;
    background:var(--surface); border:1px solid var(--line);
    border-radius:18px; padding:16px; width:300px;
    box-shadow:0 8px 32px rgba(0,0,0,0.6);
    animation: slideUp 0.2s ease;
  `;
  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="font-size:14px;font-weight:700;color:var(--accent);">🎨 Theme Badlo</span>
      <button onclick="document.getElementById('themePanel').remove()" style="background:none;border:none;color:var(--text-dim);font-size:18px;cursor:pointer;">✕</button>
    </div>
    <div id="themeGrid" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;"></div>
    <div style="border-top:1px solid var(--line);padding-top:12px;">
      <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">✨ AI se apna theme banao</div>
      <div style="display:flex;gap:8px;">
        <input id="aiThemeInput" placeholder="Jaise: neon cyberpunk, sunset orange..." 
          style="flex:1;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 10px;color:var(--text);font-size:12px;outline:none;">
        <button id="aiThemeBtn" onclick="generateAITheme()" 
          style="background:var(--accent);color:#000;border:none;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:700;cursor:pointer;">Go</button>
      </div>
      <div id="aiThemeStatus" style="font-size:11px;color:var(--text-dim);margin-top:6px;"></div>
    </div>
  `;

  // Preset buttons banana
  const grid = panel.querySelector('#themeGrid');
  Object.entries(JARVIS_THEMES).forEach(([key, t]) => {
    const btn = document.createElement('button');
    btn.style.cssText = `
      background:var(--bg); border:1px solid ${_currentTheme===key ? 'var(--accent)' : 'var(--line)'};
      border-radius:10px; padding:8px; font-size:11px; color:var(--text);
      cursor:pointer; text-align:left; transition:border-color 0.2s;
    `;
    btn.textContent = t.label;
    btn.onclick = () => {
      applyTheme(key);
      _aiThemeActive = false;
      document.querySelectorAll('#themeGrid button').forEach(b => b.style.borderColor='var(--line)');
      btn.style.borderColor = 'var(--accent)';
    };
    grid.appendChild(btn);
  });

  document.body.appendChild(panel);
}

async function generateAITheme() {
  const input = document.getElementById('aiThemeInput');
  const status = document.getElementById('aiThemeStatus');
  const btn = document.getElementById('aiThemeBtn');
  const desc = (input?.value || '').trim();
  if (!desc) { if(status) status.textContent = 'Kuch likhkar batao kaisa theme chahiye!'; return; }

  if(btn) { btn.disabled=true; btn.textContent='...'; }
  if(status) status.textContent = '🤖 AI theme bana raha hai...';

  try {
    const res = await fetch('/api/generate_theme', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ description: desc })
    });
    const data = await res.json();

    if (!data.success) {
      throw new Error(data.message || 'Theme generate nahi hui');
    }

    const themeVars = data.vars;
    // Validate karo
    const required = ['--bg','--surface','--line','--text','--text-dim','--accent','--accent2','--msg-user','--msg-jarvis'];
    const hasAll = required.every(k => themeVars[k]);
    if (!hasAll) throw new Error('Incomplete theme');

    applyTheme('custom', themeVars);
    localStorage.setItem('jarvisCustomTheme', JSON.stringify(themeVars));
    _aiThemeActive = true;
    if(status) status.textContent = `✅ "${desc}" theme apply ho gayi!`;
  } catch(e) {
    if(status) status.textContent = `❌ Error: ${e.message}. Phir try karo.`;
  } finally {
    if(btn) { btn.disabled=false; btn.textContent='Go'; }
  }
}

// Theme button — settings ke paas add karo
(function addThemeButton() {
  const settingsBtn = document.getElementById('settingsBtn');
  if (!settingsBtn) return;
  const themeBtn = document.createElement('button');
  themeBtn.id = 'themeBtn';
  themeBtn.title = 'Theme badlo';
  themeBtn.innerHTML = '🎨';
  themeBtn.style.cssText = `
    background:none; border:none; font-size:20px; cursor:pointer;
    padding:8px; opacity:0.7; transition:opacity 0.2s;
  `;
  themeBtn.addEventListener('mouseenter', () => themeBtn.style.opacity='1');
  themeBtn.addEventListener('mouseleave', () => themeBtn.style.opacity='0.7');
  themeBtn.addEventListener('click', openThemePanel);
  settingsBtn.parentElement.insertBefore(themeBtn, settingsBtn);
})();

// ═══════════════════════════════════════════════════
// CHAT BACKGROUND IMAGE SYSTEM
// ═══════════════════════════════════════════════════

function setChatBackgroundImage(src, isLocal, opts) {
  const chatArea = document.getElementById('chat') || document.querySelector('.chat');
  if (!chatArea) return;

  opts = opts || {};
  const fit      = opts.fit      || localStorage.getItem('jarvisChatBgFit')      || 'cover';
  const position = opts.position || localStorage.getItem('jarvisChatBgPosition') || 'center';
  const opacity  = opts.opacity  !== undefined ? opts.opacity : parseFloat(localStorage.getItem('jarvisChatBgOpacity') || '0.55');

  const finalUrl = isLocal ? src : ('/api/imgproxy?url=' + encodeURIComponent(src));

  chatArea.style.backgroundImage = `linear-gradient(rgba(0,0,0,${opacity}), rgba(0,0,0,${opacity})), url('${finalUrl}')`;
  chatArea.style.backgroundSize = (fit === 'stretch') ? '100% 100%' : fit;
  chatArea.style.backgroundPosition = position;
  chatArea.style.backgroundAttachment = 'fixed';
  chatArea.style.backgroundRepeat = fit === 'repeat' ? 'repeat' : 'no-repeat';

  localStorage.setItem('jarvisChatBgImage', src);
  localStorage.setItem('jarvisChatBgIsLocal', isLocal ? '1' : '0');
  localStorage.setItem('jarvisChatBgFit', fit);
  localStorage.setItem('jarvisChatBgPosition', position);
  localStorage.setItem('jarvisChatBgOpacity', String(opacity));

  if (!opts.silent) showBgToast('✅ Background set ho gaya!');
}

function updateBgFit(fit) {
  const src = localStorage.getItem('jarvisChatBgImage');
  const isLocal = localStorage.getItem('jarvisChatBgIsLocal') === '1';
  if (src) setChatBackgroundImage(src, isLocal, { fit, silent: true });
}

function updateBgPosition(position) {
  const src = localStorage.getItem('jarvisChatBgImage');
  const isLocal = localStorage.getItem('jarvisChatBgIsLocal') === '1';
  if (src) setChatBackgroundImage(src, isLocal, { position, silent: true });
}

function updateBgOpacity(opacity) {
  const src = localStorage.getItem('jarvisChatBgImage');
  const isLocal = localStorage.getItem('jarvisChatBgIsLocal') === '1';
  if (src) setChatBackgroundImage(src, isLocal, { opacity: parseFloat(opacity), silent: true });
}

function openBgControls() {
  const existing = document.getElementById('bgControlsPanel');
  if (existing) { existing.remove(); return; }

  const src = localStorage.getItem('jarvisChatBgImage');
  if (!src) { showBgToast('Pehle koi background set karo!'); return; }

  const fit      = localStorage.getItem('jarvisChatBgFit') || 'cover';
  const position = localStorage.getItem('jarvisChatBgPosition') || 'center';
  const opacity  = localStorage.getItem('jarvisChatBgOpacity') || '0.55';

  const panel = document.createElement('div');
  panel.id = 'bgControlsPanel';
  panel.style.cssText = `
    position:fixed; bottom:80px; right:16px; z-index:9999;
    background:var(--surface); border:1px solid var(--line);
    border-radius:18px; padding:16px; width:280px;
    box-shadow:0 8px 32px rgba(0,0,0,0.6);
  `;

  const fitOptions = [
    { v: 'cover',   l: '🔲 Cover (fill, crop)' },
    { v: 'contain', l: '🖼️ Contain (fit fully)' },
    { v: 'stretch' , l: '↔️ Stretch (fill exactly)' },
    { v: 'auto'    , l: '⚪ Original size' },
    { v: 'repeat'  , l: '🔁 Repeat (tile)' },
  ];
  const posOptions = [
    { v: 'center',       l: 'Center' },
    { v: 'top',          l: 'Top' },
    { v: 'bottom',        l: 'Bottom' },
    { v: 'left',          l: 'Left' },
    { v: 'right',         l: 'Right' },
  ];

  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <span style="font-size:14px;font-weight:700;color:var(--accent);">🖼️ Background Adjust Karo</span>
      <button onclick="document.getElementById('bgControlsPanel').remove()" style="background:none;border:none;color:var(--text-dim);font-size:18px;cursor:pointer;">✕</button>
    </div>

    <div style="margin-bottom:12px;">
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">Fit / Size</div>
      <select id="bgFitSelect" style="width:100%;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;color:var(--text);font-size:12px;">
        ${fitOptions.map(o => `<option value="${o.v}" ${o.v===fit?'selected':''}>${o.l}</option>`).join('')}
      </select>
    </div>

    <div style="margin-bottom:12px;">
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">Position</div>
      <select id="bgPosSelect" style="width:100%;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;color:var(--text);font-size:12px;">
        ${posOptions.map(o => `<option value="${o.v}" ${o.v===position?'selected':''}>${o.l}</option>`).join('')}
      </select>
    </div>

    <div style="margin-bottom:8px;">
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">Darkness (text readability) — <span id="bgOpacityVal">${Math.round(opacity*100)}%</span></div>
      <input type="range" id="bgOpacitySlider" min="0" max="0.9" step="0.05" value="${opacity}" style="width:100%;">
    </div>
  `;

  document.body.appendChild(panel);

  document.getElementById('bgFitSelect').addEventListener('change', (e) => updateBgFit(e.target.value));
  document.getElementById('bgPosSelect').addEventListener('change', (e) => updateBgPosition(e.target.value));
  const slider = document.getElementById('bgOpacitySlider');
  slider.addEventListener('input', (e) => {
    document.getElementById('bgOpacityVal').textContent = Math.round(e.target.value * 100) + '%';
    updateBgOpacity(e.target.value);
  });
}

function clearChatBackgroundImage() {
  const chatArea = document.getElementById('chat') || document.querySelector('.chat');
  if (chatArea) {
    chatArea.style.backgroundImage = '';
  }
  localStorage.removeItem('jarvisChatBgImage');
  localStorage.removeItem('jarvisChatBgIsLocal');
  showBgToast('🗑️ Background hata diya');
}

function showBgToast(msg) {
  const toast = document.createElement('div');
  toast.textContent = msg;
  toast.style.cssText = 'position:fixed;top:70px;left:50%;transform:translateX(-50%);background:var(--surface);color:var(--text);border:1px solid var(--accent);border-radius:20px;padding:8px 18px;font-size:12px;z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,0.4);';
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2200);
}

// Page load pe saved background restore karo
(function restoreChatBackground() {
  const savedSrc = localStorage.getItem('jarvisChatBgImage');
  const isLocal = localStorage.getItem('jarvisChatBgIsLocal') === '1';
  if (savedSrc) {
    setTimeout(() => setChatBackgroundImage(savedSrc, isLocal, { silent: true }), 300);
  }
})();

// ── AI se background image generate karo (theme panel se) ──
async function generateAIBackground() {
  const input = document.getElementById('aiThemeInput');
  const status = document.getElementById('aiThemeStatus');
  const btn = document.getElementById('aiBgBtn');
  const desc = (input?.value || '').trim();
  if (!desc) { if (status) status.textContent = 'Pehle batao kaisi image chahiye!'; return; }

  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  if (status) status.textContent = '🎨 AI image bana raha hai, thoda wait karo...';

  try {
    const res = await fetch('/api/generate_bg_image', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: desc })
    });
    const data = await res.json();

    if (!data.success) {
      throw new Error(data.message || 'Image generate nahi hui');
    }

    setChatBackgroundImage(data.path, true);
    setTimeout(openBgControls, 400);
    if (status) status.textContent = `✅ "${desc}" background set ho gaya!`;
  } catch (e) {
    if (status) status.textContent = `❌ Error: ${e.message}`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🖼️ BG'; }
  }
}

// Theme panel mein background section inject karo
const _origOpenThemePanel = openThemePanel;
window.openThemePanel = function() {
  _origOpenThemePanel();
  const panel = document.getElementById('themePanel');
  if (!panel) return;
  if (document.getElementById('bgSection')) return; // already added

  const aiSection = panel.querySelector('div[style*="border-top"]');
  if (!aiSection) return;

  const bgSection = document.createElement('div');
  bgSection.id = 'bgSection';
  bgSection.style.cssText = 'border-top:1px solid var(--line);padding-top:12px;margin-top:12px;';
  bgSection.innerHTML = `
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">🖼️ Chat background — AI se image banao (upar wala text box use karo)</div>
    <div style="display:flex;gap:8px;">
      <button id="aiBgBtn" onclick="generateAIBackground()"
        style="flex:1;background:var(--accent2);color:#fff;border:none;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:700;cursor:pointer;">🖼️ BG Banao</button>
      <button onclick="openBgControls()"
        style="background:var(--bg);border:1px solid var(--accent);color:var(--accent);border-radius:10px;padding:8px 12px;font-size:12px;cursor:pointer;">⚙️ Adjust</button>
      <button onclick="clearChatBackgroundImage()"
        style="background:var(--bg);border:1px solid var(--line);color:var(--text-dim);border-radius:10px;padding:8px 12px;font-size:12px;cursor:pointer;">Hatao</button>
    </div>
  `;
  aiSection.parentElement.appendChild(bgSection);
};
