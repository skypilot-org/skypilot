// Google Tag Manager
(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-WDLMDR6J');

document.addEventListener('DOMContentLoaded', function () {
       var script = document.createElement('script');
       script.src = 'https://widget.kapa.ai/kapa-widget.bundle.js';
       script.setAttribute('data-website-id', '4223d017-a3d2-4b92-b191-ea4d425a23c3');
       script.setAttribute('data-project-name', 'SkyPilot');
       script.setAttribute('data-project-color', '#4C4C4D');
       script.setAttribute('data-project-logo', 'https://avatars.githubusercontent.com/u/109387420?s=100&v=4');
       script.setAttribute('data-modal-disclaimer', 'Results are automatically generated and may be inaccurate or contain inappropriate information. Do not include any sensitive information in your query.\n**To get further assistance, you can chat directly with the development team** by joining the [SkyPilot Slack](https://slack.skypilot.co/).');
       script.setAttribute('data-modal-title', 'SkyPilot Docs AI - Ask a Question.');
       script.setAttribute('data-button-position-bottom', '100px');
       script.setAttribute('data-user-analytics-fingerprint-enabled', 'true');
       script.async = true;
       document.head.appendChild(script);
});

(function(h,o,t,j,a,r){
       h.hj=h.hj||function(){(h.hj.q=h.hj.q||[]).push(arguments)};
       h._hjSettings={hjid:3768396,hjsv:6};
       a=o.getElementsByTagName('head')[0];
       r=o.createElement('script');r.async=1;
       r.src=t+h._hjSettings.hjid+j+h._hjSettings.hjsv;
       a.appendChild(r);
})(window,document,'https://static.hotjar.com/c/hotjar-','.js?sv=');
!function(t){var k="ko",i=(window.globalKoalaKey=window.globalKoalaKey||k);if(window[i])return;var ko=(window[i]=[]);["identify","track","removeListeners","on","off","qualify","ready"].forEach(function(t){ko[t]=function(){var n=[].slice.call(arguments);return n.unshift(t),ko.push(n),ko}});var n=document.createElement("script");n.async=!0,n.setAttribute("src","https://cdn.getkoala.com/v1/pk_d9bb6290ccb8a01b2d181fc0c8cf0dbb9836/sdk.js"),(document.body || document.head).appendChild(n)}();

// New items: add 'new-item' class for for new items.
document.addEventListener('DOMContentLoaded', () => {
    // New items:
    const newItems = [
        { selector: '.toctree-l2 > a', text: 'HTTPS Encryption' },
        { selector: '.toctree-l2 > a', text: 'Upgrading API Server' },
        { selector: '.toctree-l1 > a', text: 'High Availability Controller' },
        { selector: '.toctree-l2 > a', text: 'High Availability Controller' },
        { selector: '.toctree-l3 > a', text: 'Advanced: High Availability Controller' },
        { selector: '.toctree-l1 > a', text: 'Using a Pool of Workers' },
        { selector: '.toctree-l1 > a', text: 'Job Groups' },
        { selector: '.toctree-l1 > a', text: 'Using Slurm' },
        { selector: '.toctree-l1 > a', text: 'SkyPilot Recipes' },
        { selector: '.toctree-l1 > a', text: 'Agent Skills' },
        { selector: '.toctree-l2 > a', text: 'Agents' },
    ];
    newItems.forEach(({ selector, text }) => {
        document.querySelectorAll(selector).forEach((el) => {
            if (el.textContent.includes(text)) {
                el.classList.add('new-item');
            }
        });
    });
});

// Remove Previous button from every first page of each tab.
document.addEventListener("DOMContentLoaded", function () {
    if (window.location.pathname.endsWith('/') || window.location.pathname.endsWith('index.html')) {
        var style = document.createElement('style');
        style.innerHTML = '.prev-next-area a.left-prev { display: none; }';
        document.head.appendChild(style);
    }
});

// Copy page as Markdown — split button next to page title.
document.addEventListener('DOMContentLoaded', () => {
    const h1 = document.querySelector('.bd-content h1');
    if (!h1) return;

    // Build the .html.md URL for the current page.
    let pagePath = window.location.pathname;
    if (pagePath.endsWith('/')) pagePath += 'index.html';
    const mdUrl = pagePath + '.md';

    // SVG icons.
    const copyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const arrowIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>';
    const chevronIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const checkIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

    // Build the split button widget.
    const wrapper = document.createElement('div');
    wrapper.className = 'copy-page-wrapper';
    wrapper.innerHTML =
        `<div class="copy-page-split">` +
            `<button class="copy-page-main" title="Copy page as Markdown">${copyIcon}<span class="copy-page-label">Copy page</span></button>` +
            `<button class="copy-page-toggle" title="More options">${chevronIcon}</button>` +
        `</div>` +
        `<div class="copy-page-dropdown">` +
            `<button class="copy-page-item" data-action="copy">${copyIcon} Copy page as Markdown</button>` +
            `<button class="copy-page-item" data-action="open">${arrowIcon} Open Markdown</button>` +
        `</div>`;

    // Insert after the h1 — position absolutely relative to h1's section.
    h1.style.position = 'relative';
    h1.appendChild(wrapper);

    const mainBtn = wrapper.querySelector('.copy-page-main');
    const toggleBtn = wrapper.querySelector('.copy-page-toggle');
    const dropdown = wrapper.querySelector('.copy-page-dropdown');
    const label = wrapper.querySelector('.copy-page-label');

    const copyMarkdown = async () => {
        const response = await fetch(mdUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const text = await response.text();
        await navigator.clipboard.writeText(text);
    };

    const showCopied = () => {
        label.textContent = 'Copied!';
        mainBtn.querySelector('svg').outerHTML = checkIcon;
        wrapper.classList.add('copy-page-success');
        setTimeout(() => {
            label.textContent = 'Copy page';
            mainBtn.querySelector('svg').outerHTML = copyIcon;
            wrapper.classList.remove('copy-page-success');
        }, 2000);
    };

    const showFailed = () => {
        label.textContent = 'Failed to copy';
        setTimeout(() => { label.textContent = 'Copy page'; }, 2000);
    };

    const handleCopy = async () => {
        try {
            await copyMarkdown();
            showCopied();
        } catch (err) {
            console.error('Failed to copy markdown:', err);
            showFailed();
        }
    };

    // Main button: copy immediately.
    mainBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        handleCopy();
    });

    // Toggle dropdown.
    toggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('open');
    });

    // Dropdown items.
    dropdown.addEventListener('click', (e) => {
        const item = e.target.closest('[data-action]');
        if (!item) return;
        e.stopPropagation();
        dropdown.classList.remove('open');
        if (item.dataset.action === 'copy') {
            handleCopy();
        } else if (item.dataset.action === 'open') {
            window.open(mdUrl, '_blank');
        }
    });

    // Close dropdown on outside click.
    document.addEventListener('click', () => {
        dropdown.classList.remove('open');
    });
});
