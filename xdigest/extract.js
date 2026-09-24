// Runs inside x.com via page.evaluate(). Returns every post currently in the
// DOM as plain data. The timeline is virtualized, so the caller scrolls and
// calls this repeatedly, de-duplicating by id.
() => {
  const METRIC_KEYS = ['replies', 'reposts', 'likes', 'bookmarks', 'views'];

  const parseMetrics = label => {
    const out = {};
    for (const part of (label || '').split(',')) {
      const m = part.trim().match(/^([\d,.]+)\s+(\w+)/);
      if (!m) continue;
      const key = METRIC_KEYS.find(k => m[2].toLowerCase().startsWith(k.slice(0, 4)));
      if (key) out[key] = Number(m[1].replace(/,/g, ''));
    }
    return out;
  };

  const userOf = el => {
    const lines = (el?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    return {
      name: lines[0] || '',
      handle: (lines.find(s => s.startsWith('@')) || '').slice(1),
    };
  };

  // A video's thumbnail can appear both as <video poster> and as an <img>; keep one.
  const imagesIn = (root, exclude) => [...new Map(
    [...root.querySelectorAll('[data-testid="tweetPhoto"] img, video[poster]')]
      .filter(el => !(exclude && exclude.contains(el)))
      .map(el => el.tagName === 'VIDEO' ? el.poster : el.src)
      .filter(src => src && src.includes('pbs.twimg.com'))
      .map(src => src.replace(/([?&])name=\w+/, '$1name=small'))
      .map(src => [src.split('?')[0], src])
  ).values()];

  return [...document.querySelectorAll('article[data-testid="tweet"]')].map(a => {
    const timeLink = a.querySelector('time')?.closest('a');
    const idMatch = timeLink?.getAttribute('href')?.match(/^\/([^/]+)\/status\/(\d+)/);

    // A quoted post renders as a nested div[role=link] with its own User-Name.
    const quoteBox = [...a.querySelectorAll('div[role="link"]')]
      .find(d => d.querySelector('[data-testid="User-Name"]'));
    const texts = [...a.querySelectorAll('[data-testid="tweetText"]')];
    const mainText = texts.find(t => !(quoteBox && quoteBox.contains(t)));
    const quoteText = quoteBox ? texts.find(t => quoteBox.contains(t)) : null;

    const card = a.querySelector('[data-testid="card.wrapper"]');
    const isAd =
      !!a.closest('[data-testid="placementTracking"]') && !timeLink ||
      [...a.querySelectorAll('span')].some(s => s.innerText.trim() === 'Ad');

    return {
      id: idMatch ? idMatch[2] : null,
      url: idMatch ? `https://x.com/${idMatch[1]}/status/${idMatch[2]}` : null,
      author: userOf(a.querySelector('[data-testid="User-Name"]')),
      created_at: a.querySelector('time')?.getAttribute('datetime') || null,
      text: mainText?.innerText || '',
      truncated: !!a.querySelector('[data-testid="tweet-text-show-more-link"]'),
      images: imagesIn(a, quoteBox),
      has_video: !!a.querySelector('[data-testid="videoPlayer"]'),
      quote: quoteBox ? {
        author: userOf(quoteBox.querySelector('[data-testid="User-Name"]')),
        text: quoteText?.innerText || '',
        images: imagesIn(quoteBox),
      } : null,
      card: card ? {
        text: card.innerText.slice(0, 300),
        href: card.querySelector('a')?.href || null,
      } : null,
      social_context: a.querySelector('[data-testid="socialContext"]')?.innerText || null,
      is_reply: /(^|\n)Replying to/.test(a.innerText),
      is_ad: isAd,
      metrics: parseMetrics(a.querySelector('[role="group"][aria-label]')?.getAttribute('aria-label')),
    };
  });
}
