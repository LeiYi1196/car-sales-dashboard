// Client-side helpers: PNG export, print-to-PDF, copy share link.

function _showToast(msg, ms) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('toast-visible');
  clearTimeout(el._hide);
  el._hide = setTimeout(() => el.classList.remove('toast-visible'), ms || 1800);
}

function _ts() {
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

async function exportPng(scope) {
  const body = document.getElementById('dashboard-body');
  if (!body) {
    _showToast('Nothing to export');
    return;
  }
  if (typeof html2canvas !== 'function') {
    _showToast('Screenshot library failed to load');
    return;
  }

  _showToast('Rendering…', 30000);

  // Plotly charts render as SVG+canvas — html2canvas sometimes misses them.
  // Swap each live Plotly container for a static <img> snapshot, then restore.
  const plots = Array.from(body.querySelectorAll('.js-plotly-plot'));
  const restorers = [];
  try {
    for (const plot of plots) {
      let dataUrl;
      try {
        dataUrl = await Plotly.toImage(plot, {
          format: 'png',
          width: plot.offsetWidth || 800,
          height: plot.offsetHeight || 400,
        });
      } catch (_) {
        continue;
      }
      const img = new Image();
      img.src = dataUrl;
      img.style.width = plot.offsetWidth + 'px';
      img.style.height = plot.offsetHeight + 'px';
      img.style.display = 'block';
      const parent = plot.parentNode;
      parent.insertBefore(img, plot);
      plot.style.display = 'none';
      restorers.push(() => { parent.removeChild(img); plot.style.display = ''; });
    }

    const canvas = await html2canvas(body, {
      scale: Math.min(window.devicePixelRatio || 1, 2),
      backgroundColor: '#ffffff',
      useCORS: true,
      logging: false,
    });
    const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `auto-sales-${scope || 'overview'}-${_ts()}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    _showToast('Downloaded ✓');
  } catch (err) {
    console.error(err);
    _showToast('Export failed — see console');
  } finally {
    restorers.forEach(fn => fn());
  }
}

function exportPdf() {
  window.print();
}

async function copyShareLink() {
  const url = window.location.href;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const ta = document.createElement('textarea');
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    _showToast('Link copied ✓');
  } catch (err) {
    console.error(err);
    _showToast('Copy failed — see console');
  }
}

// Expose to inline onclick handlers.
window.exportPng = exportPng;
window.exportPdf = exportPdf;
window.copyShareLink = copyShareLink;
