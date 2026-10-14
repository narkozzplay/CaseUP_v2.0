(function () {
    'use strict';

    /* Profile appearance: try-on preview, hover video, title filter.
       Delegated listeners, so it keeps working after dashboard partial navigation. */

    function stageOf(root) {
        var stage = root.querySelector('[data-pcz-stage]');
        if (!stage) {
            return null;
        }
        if (!stage.pczInitial) {
            var media = stage.querySelector('[data-pcz-stage-media]');
            var video = stage.querySelector('[data-pcz-stage-video]');
            var avatar = stage.querySelector('[data-pcz-stage-avatar]');
            stage.pczInitial = {
                background: media ? media.style.backgroundImage : '',
                video: video && !video.hidden ? video.getAttribute('src') || '' : '',
                poster: video ? video.getAttribute('poster') || '' : '',
                avatar: avatar ? avatar.getAttribute('src') || '' : ''
            };
        }
        return stage;
    }

    function setBackground(stage, staticUrl, videoUrl) {
        var media = stage.querySelector('[data-pcz-stage-media]');
        var video = stage.querySelector('[data-pcz-stage-video]');
        if (media) {
            media.style.backgroundImage = staticUrl ? 'url("' + staticUrl.replace(/"/g, '%22') + '")' : '';
        }
        if (!video) {
            return;
        }
        if (videoUrl) {
            video.setAttribute('poster', staticUrl || '');
            if (video.getAttribute('src') !== videoUrl) {
                video.setAttribute('src', videoUrl);
            }
            video.hidden = false;
            var playing = video.play();
            if (playing && playing.catch) {
                playing.catch(function () {});
            }
        } else {
            video.pause();
            video.removeAttribute('src');
            video.load();
            video.hidden = true;
        }
    }

    function setTag(stage, preview) {
        var tag = stage.querySelector('[data-pcz-stage-tag]');
        if (tag) {
            tag.textContent = preview ? tag.dataset.labelPreview : tag.dataset.labelLive;
        }
        stage.classList.toggle('is-preview', preview);
    }

    function resetPreview(root) {
        var stage = stageOf(root);
        if (!stage) {
            return;
        }
        var initial = stage.pczInitial;
        setBackground(stage, initial.poster, initial.video);
        var media = stage.querySelector('[data-pcz-stage-media]');
        if (media) {
            media.style.backgroundImage = initial.background;
        }
        var avatar = stage.querySelector('[data-pcz-stage-avatar]');
        if (avatar) {
            avatar.setAttribute('src', initial.avatar);
        }
        setTag(stage, false);
        root.querySelectorAll('[data-pcz-item].is-trying').forEach(function (card) {
            card.classList.remove('is-trying');
        });
        var tryBox = root.querySelector('[data-pcz-try]');
        if (tryBox) {
            tryBox.hidden = true;
        }
    }

    function tryOn(root, card) {
        var stage = stageOf(root);
        if (!stage) {
            return;
        }
        if (card.dataset.kind === 'bg') {
            setBackground(stage, card.dataset.static || '', card.dataset.video || '');
        } else {
            var avatar = stage.querySelector('[data-pcz-stage-avatar]');
            if (avatar) {
                avatar.setAttribute('src', card.dataset.avatar || '');
            }
        }
        setTag(stage, true);
        root.querySelectorAll('[data-pcz-item].is-trying').forEach(function (other) {
            other.classList.remove('is-trying');
        });
        card.classList.add('is-trying');

        var tryBox = root.querySelector('[data-pcz-try]');
        if (!tryBox) {
            return;
        }
        var title = tryBox.querySelector('[data-pcz-try-title]');
        if (title) {
            title.textContent = card.dataset.name || '';
        }
        var form = tryBox.querySelector('[data-pcz-apply-form]');
        var isCurrent = card.classList.contains('is-current');
        if (form) {
            form.hidden = !card.dataset.selectUrl || isCurrent;
            form.setAttribute('action', card.dataset.selectUrl || '');
            var next = form.querySelector('[data-pcz-apply-next]');
            if (next) {
                next.value = window.location.pathname + window.location.search;
            }
        }
        tryBox.hidden = false;
        if (window.matchMedia('(max-width: 1100px)').matches) {
            stage.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    document.addEventListener('click', function (event) {
        var root = event.target.closest('[data-pcz]');
        if (!root) {
            return;
        }
        var reset = event.target.closest('[data-pcz-reset]');
        if (reset) {
            resetPreview(root);
            return;
        }
        var trigger = event.target.closest('[data-pcz-preview]');
        if (!trigger) {
            return;
        }
        var card = trigger.closest('[data-pcz-item]');
        if (card && !card.classList.contains('is-hidden')) {
            tryOn(root, card);
        }
    });

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (form && form.matches('[data-pcz-confirm]') && !window.confirm(form.dataset.pczConfirm)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    /* hover video on background cards: nothing is downloaded until hover */

    document.addEventListener('mouseover', function (event) {
        var media = event.target.closest('.pcz-card__media');
        if (!media || media.contains(event.relatedTarget)) {
            return;
        }
        var video = media.querySelector('[data-pcz-hover-video]');
        if (!video) {
            return;
        }
        var playing = video.play();
        video.classList.add('is-playing');
        if (playing && playing.catch) {
            playing.catch(function () {
                video.classList.remove('is-playing');
            });
        }
    });

    document.addEventListener('mouseout', function (event) {
        var media = event.target.closest('.pcz-card__media');
        if (!media || media.contains(event.relatedTarget)) {
            return;
        }
        var video = media.querySelector('[data-pcz-hover-video]');
        if (video) {
            video.pause();
            video.classList.remove('is-playing');
        }
    });

    /* filter by title */

    document.addEventListener('input', function (event) {
        var input = event.target.closest('[data-pcz-filter]');
        if (!input) {
            return;
        }
        var root = input.closest('[data-pcz]');
        var query = input.value.trim().toLowerCase();
        var visible = 0;
        root.querySelectorAll('[data-pcz-grid] [data-pcz-item]').forEach(function (card) {
            var match = !query || (card.dataset.title || '').indexOf(query) !== -1;
            card.hidden = !match;
            if (match) {
                visible += 1;
            }
        });
        var empty = root.querySelector('[data-pcz-filter-empty]');
        if (empty) {
            empty.hidden = visible !== 0;
        }
    });
})();
