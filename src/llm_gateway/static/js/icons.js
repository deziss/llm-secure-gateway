/**
 * Lucide Icons Adapter for LLM Gateway
 * Replaces FontAwesome dependency with modern, lightweight Lucide vector SVG icons.
 */
(function () {
    const faToLucideMap = {
        'fa-shield-alt': 'shield',
        'fa-shield': 'shield',
        'fa-server': 'server',
        'fa-chart-line': 'line-chart',
        'fa-users-cog': 'users',
        'fa-user-friends': 'users',
        'fa-user-lock': 'user-check',
        'fa-user-plus': 'user-plus',
        'fa-user-edit': 'user-pen',
        'fa-user-shield': 'shield-check',
        'fa-user-tie': 'user',
        'fa-users': 'users',
        'fa-user': 'user',
        'fa-flask': 'flask-conical',
        'fa-columns': 'columns',
        'fa-robot': 'bot',
        'fa-database': 'database',
        'fa-cubes': 'boxes',
        'fa-cube': 'box',
        'fa-key': 'key',
        'fa-tags': 'tags',
        'fa-tag': 'tag',
        'fa-dollar-sign': 'dollar-sign',
        'fa-cog': 'settings',
        'fa-cogs': 'settings',
        'fa-gear': 'settings',
        'fa-sign-out-alt': 'log-out',
        'fa-sign-in-alt': 'log-in',
        'fa-sync': 'refresh-cw',
        'fa-sync-alt': 'refresh-cw',
        'fa-trash': 'trash-2',
        'fa-trash-alt': 'trash-2',
        'fa-edit': 'edit-3',
        'fa-plus': 'plus',
        'fa-plus-circle': 'plus-circle',
        'fa-paper-plane': 'send',
        'fa-check': 'check',
        'fa-check-circle': 'check-circle',
        'fa-exclamation-circle': 'alert-circle',
        'fa-exclamation-triangle': 'alert-triangle',
        'fa-times': 'x',
        'fa-times-circle': 'x-circle',
        'fa-search': 'search',
        'fa-download': 'download',
        'fa-cloud-download-alt': 'cloud-download',
        'fa-eye': 'eye',
        'fa-eye-slash': 'eye-off',
        'fa-lock': 'lock',
        'fa-network-wired': 'network',
        'fa-satellite-dish': 'radio',
        'fa-terminal': 'terminal',
        'fa-stream': 'activity',
        'fa-telegram': 'send',
        'fa-discord': 'message-square',
        'fa-slack': 'hash',
        'fa-spinner': 'loader-2',
        'fa-circle-notch': 'loader-2',
        'fa-copy': 'copy',
        'fa-code': 'code',
        'fa-comment-dots': 'message-square',
        'fa-info-circle': 'info',
        'fa-arrow-left': 'arrow-left',
        'fa-arrow-up': 'arrow-up',
        'fa-arrows-alt': 'maximize-2',
        'fa-chevron-down': 'chevron-down',
        'fa-link': 'link',
        'fa-list': 'list',
        'fa-history': 'history',
        'fa-eraser': 'eraser',
        'fa-microchip': 'cpu',
        'fa-envelope': 'mail',
        'fa-id-card': 'credit-card',
        'fa-bullseye': 'target',
        'fa-project-diagram': 'git-branch',
        'fa-toggle-on': 'toggle-right',
        'fa-ghost': 'ghost',
        'fa-globe': 'globe',
        'fa-inbox': 'inbox',
        'fa-calendar-plus': 'calendar-plus',
        'fa-ticket-alt': 'ticket',
        'fa-mouse-pointer': 'mouse-pointer',
        'fa-palette': 'palette',
        'fa-sun': 'sun',
        'fa-moon': 'moon',
        'fa-desktop': 'monitor',
        'fa-fingerprint': 'fingerprint',
        'fa-brain': 'brain',
        'fa-shield-virus': 'shield-alert',
        'fa-vial': 'flask-conical',
        'fa-clipboard-check': 'clipboard-check',
        'fa-sitemap': 'network',
        'fa-magic': 'sparkles',
        'fa-coins': 'coins',
        'fa-arrow-down': 'arrow-down',
        'fa-bolt': 'zap',
        'fa-image': 'image'
    };

    function adaptIcons(root) {
        if (!root) root = document;
        // Find any <i> or <span> with fa-* classes that don't yet have data-lucide
        const elements = root.querySelectorAll ? root.querySelectorAll('i[class*="fa-"]:not([data-lucide])') : [];
        elements.forEach(el => {
            const classList = Array.from(el.classList);
            for (const cls of classList) {
                if (faToLucideMap[cls]) {
                    el.setAttribute('data-lucide', faToLucideMap[cls]);
                    if (el.classList.contains('fa-spin')) {
                        el.classList.add('lucide-spin');
                    }
                    break;
                }
            }
        });

        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    }

    window.refreshIcons = function(root) {
        adaptIcons(root);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => adaptIcons());
    } else {
        adaptIcons();
    }

    // Auto-observe DOM changes for dynamic tables, modals, and toasts
    if (typeof MutationObserver !== 'undefined') {
        let timer = null;
        const observer = new MutationObserver((mutations) => {
            let shouldRefresh = false;
            for (const m of mutations) {
                if (m.addedNodes && m.addedNodes.length > 0) {
                    shouldRefresh = true;
                    break;
                }
            }
            if (shouldRefresh) {
                clearTimeout(timer);
                timer = setTimeout(() => adaptIcons(), 50);
            }
        });

        if (document.body) {
            observer.observe(document.body, { childList: true, subtree: true });
        } else {
            document.addEventListener('DOMContentLoaded', () => {
                observer.observe(document.body, { childList: true, subtree: true });
            });
        }
    }
})();
