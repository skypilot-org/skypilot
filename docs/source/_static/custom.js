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
