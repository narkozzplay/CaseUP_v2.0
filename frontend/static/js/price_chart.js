(function () {
    'use strict';

    const cache = new WeakMap();

    function chartData(chart) {
        if (cache.has(chart)) {
            return cache.get(chart);
        }
        const script = chart.querySelector('script[type="application/json"]');
        let points = [];
        try {
            points = JSON.parse(script ? script.textContent : '[]');
        } catch (error) {
            points = [];
        }
        cache.set(chart, points);
        return points;
    }

    function nearestPoint(points, xPercent) {
        let best = points[0];
        let bestDistance = Infinity;
        points.forEach((point) => {
            const distance = Math.abs(point.x - xPercent);
            if (distance < bestDistance) {
                best = point;
                bestDistance = distance;
            }
        });
        return best;
    }

    function hide(plot) {
        plot.querySelectorAll('[data-price-chart-cursor], [data-price-chart-cursor-dot], [data-price-chart-tooltip]').forEach((node) => {
            node.hidden = true;
        });
    }

    function show(plot, clientX) {
        const chart = plot.closest('[data-price-chart]');
        const points = chartData(chart);
        if (!points.length) {
            return;
        }
        const rect = plot.getBoundingClientRect();
        const xPercent = ((clientX - rect.left) / rect.width) * 100;
        const point = points.length === 1 ? { ...points[0], x: 50 } : nearestPoint(points, xPercent);

        const cursor = plot.querySelector('[data-price-chart-cursor]');
        const dot = plot.querySelector('[data-price-chart-cursor-dot]');
        const tooltip = plot.querySelector('[data-price-chart-tooltip]');
        const currency = chart.dataset.currency || '';

        cursor.style.left = `${point.x}%`;
        dot.style.left = `${point.x}%`;
        dot.style.top = `${point.y}%`;

        let change = '';
        if (point.note) {
            change = `<span class="pc__tooltip-change pc__tooltip-change--${point.dir}">${point.note}</span>`;
        } else if (point.change) {
            change = `<span class="pc__tooltip-change pc__tooltip-change--${point.dir}">${point.change} к прошлой</span>`;
        }
        tooltip.innerHTML = `<span class="pc__tooltip-price">${point.price} ${currency}</span><span class="pc__tooltip-date">${point.date}</span>${change}`;

        cursor.hidden = false;
        dot.hidden = false;
        tooltip.hidden = false;

        const pointX = (point.x / 100) * rect.width;
        const tooltipWidth = tooltip.offsetWidth;
        let left = pointX + 14;
        if (left + tooltipWidth > rect.width) {
            left = pointX - tooltipWidth - 14;
        }
        tooltip.style.left = `${Math.max(0, left)}px`;
        const pointY = (point.y / 100) * rect.height;
        tooltip.style.top = `${Math.min(Math.max(0, pointY - tooltip.offsetHeight - 12), rect.height - tooltip.offsetHeight)}px`;
    }

    document.addEventListener('pointermove', (event) => {
        const plot = event.target.closest && event.target.closest('[data-price-chart-plot]');
        document.querySelectorAll('[data-price-chart-plot]').forEach((node) => {
            if (node !== plot) {
                hide(node);
            }
        });
        if (plot) {
            show(plot, event.clientX);
        }
    });

    document.addEventListener('pointerleave', (event) => {
        if (event.target.matches && event.target.matches('[data-price-chart-plot]')) {
            hide(event.target);
        }
    }, true);
})();
