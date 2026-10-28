<!-- SPDX-License-Identifier: GPL-2.0 -->
# Готовый каркас для веб-зеркала GTK-интерфейса: исследование

Дата: 2026-10-28.

Вопрос: есть ли готовый каркас, как можно более близкий к GTK, который
избавил бы «Видео» от написания каждого экрана дважды (GTK 3 в
`haltia-video` и рукописный клиент в `haltia_video/adapters/web/static/`).

Требования берутся из [embedded-web.md](../architecture/embedded-web.md):
несколько независимых браузеров одновременно, у каждого своя навигация;
подключение по коду; собственный HTML5 `<video>` через медиапрокси, без
перекодирования; полноэкранный режим; касания на Android 11, Chrome/Firefox
Android и iPad Safari; одна Library с одним владельцем; ничего тяжёлого, пока
веб выключен; GTK на B8000-H (armv7, 1 ГБ) не получает веб-зависимостей.

Короткий ответ: **такого каркаса нет.** Единственное, что действительно
показывает *существующий* GTK-код в браузере, — Broadway, — не подходит по
трём независимым причинам (один браузер на дисплей, нет видео, объявлен к
удалению). Всё остальное — это второй UI на другом API, то есть та же работа
«написать экран ещё раз», только на чужом каркасе и с тяжёлыми зависимостями.
Честная рекомендация — оставить рукописный клиент и делить с GTK не виджеты, а
модели страниц (см. последний раздел).

## 1. GTK Broadway (GTK 3.24 и GTK 4)

**Что это.** Бэкенд GDK, который рисует окна приложения в браузере через
WebSocket: `broadwayd :5`, приложение с `GDK_BACKEND=broadway
BROADWAY_DISPLAY=:5`, браузер на порт `8080 + display`
([GTK 3: Broadway](https://docs.gtk.org/gtk3/broadway.html),
[GTK 4: Broadway](https://docs.gtk.org/gtk4/broadway.html)). Это единственный
вариант, при котором код окна переиспользуется целиком.

**Модель отрисовки.**
- GTK 3: каждое окно — `<canvas>`; сервер шлёт пиксельный буфер, закодированный
  относительно предыдущего кадра и сжатый raw-deflate (`broadway_buffer_encode`,
  `GZlibCompressor`), клиент распаковывает и вызывает `putImageData`
  ([broadway-output.c, gtk-3-24](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/broadway-output.c),
  [broadway.js, gtk-3-24](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/broadway.js)).
  Это по сути удалённый экран: каждое изменение карточки, прокрутка, анимация —
  кадр с ПК.
- GTK 4: дерево узлов отрисовки превращается в DOM (`div` на узел), текстуры
  уходят как PNG и показываются `img` через blob/data URL
  ([broadway.js, main](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/broadway/broadway.js)).
  Меньше пикселей по сети, но каждая обложка — PNG-текстура, отправленная
  сервером.

**Несколько независимых браузеров — нет.** В `broadway-server.c` новое
WebSocket-подключение вытесняет текущее: `start()` вызывает
`broadway_output_disconnected` для старого вывода, освобождает старый вход и
пересылает состояние окон новому клиенту — и в 3.24, и в main
([gtk-3-24](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/broadway-server.c),
[main](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/broadway/broadway-server.c)).
Один дисплей = один браузер; второй планшет «отнимает» экран у первого.
Несколько *приложений* на одном дисплее — да, это документированный сценарий
([gtk4-broadwayd](https://docs.gtk.org/gtk4/gtk4-broadwayd.html)).
Независимые браузеры значит: по `broadwayd` и по процессу «Видео» на каждого —
а это две Library, пишущие одни и те же `video.json`/`playback.json`, прямо
против правила «один владелец» в embedded-web.md. Открыть несколько
Broadway-дисплеев в одном процессе код `_gdk_broadway_display_open` не
запрещает ([gdkdisplay-broadway.c](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/gdkdisplay-broadway.c)),
но такой сценарий нигде не документирован, а окно «Видео» и его `self.page`/
`self.sub` рассчитаны на одно окно — пришлось бы переписать сам `haltia-video`.

**Подключение и безопасность — нет своей.** Сервер требует лишь `Host` и
`Sec-WebSocket-Key`; `Origin` читается и отражается, но не проверяется; пароля
нет, есть только TLS при `--cert`/`--key`
([broadway-server.c](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/broadway/broadway-server.c),
[broadwayd.c, gtk-3-24](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/broadwayd.c)).
Есть `--unixsocket`, так что можно спрятать его за собственным
aiohttp-прокси с нашим подключением по коду — это решаемо, но работа наша.

**Касания и экранная клавиатура — есть.** `touchstart/move/end` пересылаются
как `BROADWAY_EVENT_TOUCH`, первое касание эмулирует мышь; для iOS и Android
Chrome создаётся скрытый `input`, который фокусируется по команде сервера
показать клавиатуру; разбор ввода через событие `input` подключён только для
Android Chrome, на iOS текст этим путём не передаётся
([broadway.js, gtk-3-24](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/gdk/broadway/broadway.js)).

**Видео — нет.** В клиенте GTK 4 нет ни `<video>`, ни узла для видео: только
текстуры, цвета, рамки, тени, градиенты, клипы, трансформации
([broadway.js, main](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/broadway/broadway.js)).
Плеер «Видео» — отдельный процесс libmpv со своим окном Wayland
(`haltia-vplay`/`mpv`), Broadway его не увидит вовсе. Остаётся либо гнать кадры
через GTK (пиксели по WebSocket, в GTK 3 — deflate-диффы целого окна), либо
рядом с Broadway всё равно держать наш медиапрокси и свой `<video>` — то есть
писать отдельную веб-страницу плеера. Ни полноэкранного режима браузера, ни
фонового воспроизведения Broadway не даёт.

**Сопровождение.** GTK 4: «Broadway was written as an experiment and is not
the most actively developed backend» ([GTK 4: Broadway](https://docs.gtk.org/gtk4/broadway.html));
в GTK 4.17.4 X11 и Broadway объявлены устаревшими: «as a clear signal that we
intend to remove them in the GTK 5»
([GTK blog, winter 2025](https://blog.gtk.org/2025/02/01/whats-new-in-gtk-winter-2025-edition/)).
В main код ещё правят вместе с общими переделками (последние коммиты в
`gdk/broadway` — 2026-10-02, «broadway: Rework throttling…»); в ветке gtk-3-24
последнее изменение `gdk/broadway` — 2022-12-08 (удаление Autotools)
([GitLab API, commits по пути](https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgtk/repository/commits?path=gdk/broadway&ref_name=gtk-3-24)).

**Alpine.** `broadwayd` входит в пакет `gtk+3.0`, `gtk4-broadwayd` — в
`gtk4.0`, в v3.24 и для armv7
([pkgs.alpinelinux.org, contents](https://pkgs.alpinelinux.org/contents?file=*broadwayd&branch=v3.24&arch=armv7)),
так что ставить ничего не нужно.

**Итог.** Близость к GTK — максимальная (это и есть GTK); переиспользование
кода — полное, но только для одного браузера и без видео. Требования
«несколько независимых браузеров», «свой `<video>`», «подключение по коду» и
«один владелец Library» он не выполняет, а сам бэкенд уходит в GTK 5. Годится
разве что как отладочный «посмотреть окно с ПК», не как веб-режим.

### Родственный путь: Xpra с HTML5-клиентом

Xpra пересылает окна X11 в браузер ([xpra-html5](https://github.com/Xpra-org/xpra-html5)),
умеет видеокодеки VP8/VP9/H.264/HEVC/AV1 и сам решает, когда переключиться на
видеокодек ([Encodings](https://github.com/Xpra-org/xpra/blob/master/docs/Usage/Encodings.md)),
есть в Alpine v3.24 (`xpra`, `xpra-webclient`)
([pkgs.alpinelinux.org](https://pkgs.alpinelinux.org/packages?name=xpra*&branch=v3.24)).
Окно mpv он показать мог бы, но ценой перекодирования экрана на ПК (против
правила «без перекодирования») и с той же проблемой «один экран — одна
навигация», так что по сути это удалённый рабочий стол, а не веб-режим.

## 2. BeeWare Toga (toga-gtk + toga-web)

**Модель.** Один Python-API виджетов с бэкендами; на Linux — `toga-gtk` в двух
вариантах, «the GTK 3 backend is the more mature of the two»
([Toga: Linux](https://toga.beeware.org/en/latest/reference/platforms/linux.html)).
Это ближе всего к «одно описание — и GTK, и HTML».

**Веб.** «The Web backend is currently proof-of-concept only. Most widgets have
not been implemented.» Работает как одностраничное приложение через PyScript
(требуются PyScript 2024.11.1+ и WebAwesome v3.3)
([Toga: Web](https://toga.beeware.org/en/latest/reference/platforms/web.html)),
то есть Python исполняется *в браузере* (Pyodide). Для нас это значит:
состояние живёт на планшете, к Library ПК всё равно нужен наш JSON API, а
загрузка интерпретатора Python в браузер Android 11 противоречит правилу
«никакого Python на клиенте» из embedded-web.md. Сессии «на браузер» тут
получаются сами собой, но ценой этого веса.

**Переиспользование.** Нулевое: окно «Видео» написано на Gtk напрямую
(≈200 обращений к `Gtk.` в `haltia-video`, `haltia_kit.dialogs`, тема `b8k`,
жесты, `Gtk.Stack`, ленивые сетки). Переход на Toga — переписать GTK-окно на
более бедный API и потерять haltia-kit, при этом веб-половина не готова.
Свежие выпуски есть (`toga-gtk`/`toga-web` 0.5.7, 2026-10-01,
[PyPI toga-web](https://pypi.org/project/toga-web/)), проект живой.

**Итог:** самая правильная идея, но веб-бэкенд — прототип; не вариант.

## 3. Python-каркасы «UI на сервере»: NiceGUI, Flet, Remi, PyWebIO, Reflex, Streamlit

Общее для всех: это **не GTK** — пришлось бы писать веб-экраны заново на их
API (Python вместо JS, но всё равно второй раз), и каждый приносит свой
HTTP-сервер вместо нашего aiohttp с подключением по коду и медиапрокси.

| Каркас | Сессия на браузер | Видео | Вес / Alpine armv7, aarch64 | Состояние |
| --- | --- | --- | --- | --- |
| NiceGUI 3.18 | да: `@ui.page` — «each user accessing the given route will see a new instance of the page» ([page](https://nicegui.io/documentation/page)) | `ui.video` поверх HTML5 `<video>`, события через `on()` ([video](https://nicegui.io/documentation/video)) | FastAPI, Starlette, uvicorn[standard], python-socketio, pydantic-core, orjson и др. ([PyPI](https://pypi.org/project/nicegui/)); пакета `py3-nicegui` в Alpine нет ([pkgs](https://pkgs.alpinelinux.org/packages?name=py3-nicegui*&branch=edge)); у orjson 3.13 нет musllinux-колёс для armv7 ([PyPI orjson](https://pypi.org/project/orjson/#files)) | активен |
| Flet 1.0.5 | да: каждое WebSocket-подключение запускает новый `main()` ([dynamic website](https://flet.dev/docs/publish/web/dynamic-website/)) | `flet-video`, все платформы включая Web; на Linux — libmpv ([Video](https://flet.dev/docs/controls/video/)) | клиент — Flutter web (CanvasKit/Skwasm, WebGL/WebGPU, [renderers](https://docs.flutter.dev/platform-integration/web/renderers)); Flutter поддерживает Chrome «Latest 2», Safari 15.6+, Linux только x64/Arm64 ([supported platforms](https://docs.flutter.dev/reference/supported-platforms)); сервер FastAPI+uvicorn ([PyPI flet-web](https://pypi.org/project/flet-web/)) | активен |
| Remi 2022.7.27 | да, `multiple_instance=True` — «different App instances» ([README](https://github.com/rawpython/remi)) | своего виджета не видно в README | без зависимостей, чистый Python | последний выпуск 2022, коммиты редки (push 2026-02) |
| PyWebIO 1.8.4 | да (скриптовая модель «вопрос-ответ») | — | tornado, user-agents ([PyPI](https://pypi.org/project/pywebio/)) | последний push 2025-04 |
| Reflex 0.10 | да | `reflex-components-react-player` в зависимостях | компилирует в React-фронтенд ([installation](https://reflex.dev/docs/getting-started/installation/)), granian, redis и десяток пакетов компонентов ([PyPI](https://pypi.org/project/reflex/)) | активен |
| Streamlit 1.66 | да, но модель «перезапуск скрипта» | `st.video` | numpy, pandas, pyarrow, altair, protobuf… ([PyPI](https://pypi.org/project/streamlit/)) | активен, не для такого UI |

Замечания по существу:

- **NiceGUI** — лучший из этой группы по модели (страница на клиента, состояние
  на сервере, `<video>` нативный, Quasar-виджеты с касаниями). Но: каждый
  клик — круг через Socket.IO до ПК; дерево элементов каждого браузера живёт
  в памяти ПК; он приносит FastAPI/uvicorn рядом с aiohttp (NiceGUI 3.18 сам
  зависит и от aiohttp); и это всё равно второй UI. Выигрыш против нынешнего
  `app.js` — только язык (Python вместо JS), не число реализаций экрана.
- **Flet** — Flutter в браузере: тяжёлый движок, рисующий на canvas вместо DOM,
  поддержка «последних двух» Chrome — встроенный браузер Android 11 этому не
  обязан соответствовать. Нативное окно Flet на B8000 невозможно (Flutter не
  поддерживает 32-битный ARM Linux), так что заменить им GTK тоже нельзя.
- **Remi** ближе всех к «виджеты на Python → HTML» и лёгок, но почти не
  развивается и не даёт ничего для видео и касаний сверх голого HTML.
- **Reflex/Streamlit** — для дашбордов и внутренних инструментов, со сборкой
  фронтенда и тяжёлыми зависимостями; не наш случай.

## 4. Внешний вид «как GTK»: Adwaita для веба

- [ncpa0cpl/adwaita-web](https://github.com/ncpa0cpl/adwaita-web) — «A web UI
  framework based on GTK's Adwaita theme» на React; репозиторий **архивирован**
  (последний push 2024-02-04, по данным GitHub API).
- Другие найденные Adwaita-CSS (adwaita.css, ADWaveCSS) — мелкие личные проекты
  без выпусков, публичных репозиториев на GitHub по указанным адресам нет.
- Libadwaita — GTK 4; «Видео» на GTK 3 и выглядит так, как велит тема рабочего
  стола (на планшете — `b8k`), так что «как Adwaita» ≠ «как Видео». CSS темы
  GTK браузеру не переносится: селекторы GTK — имена узлов виджетов, а не HTML.

Итог: брать нечего; проще держать в `app.css` небольшой набор переменных
(отступы, размеры касаний, цвета), выписанных из темы `b8k`.

## 5. Одно описание UI → GTK и HTML; GtkBuilder → HTML

- Готового преобразователя GtkBuilder `.ui` → HTML не нашлось;
  `gtk4-builder-tool render` выдаёт только PNG или файл узлов
  ([gtk4-builder-tool](https://docs.gtk.org/gtk4/gtk4-builder-tool.html)). И
  он бесполезен: «Видео» строит виджеты в коде, `.ui`-файлов нет.
- Из «одно описание — два бэкенда» живым является только Toga (раздел 2), и
  его веб — прототип.

## Сводка

| Вариант | Близость к GTK | Переиспользует окно | Независимые браузеры | `<video>`/fullscreen | Android-касания | Вес для Alpine | Зрелость |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Broadway GTK 3/4 | это GTK | целиком | нет (новый вытесняет старого) | нет | есть | уже в пакетах gtk | устарел, уйдёт в GTK 5 |
| Xpra HTML5 | любое X11-окно | целиком | нет (один экран) | перекодирование экрана | ? | есть в Alpine | зрелый, но не для этого |
| Toga | свой API поверх GTK | нет, переписать | да (Python в браузере) | нет виджета | WebAwesome | Pyodide в браузере | веб — прототип |
| NiceGUI | нет | нет | да | да | Quasar | FastAPI/uvicorn/pydantic-core | активен |
| Flet | нет | нет | да | да | Flutter | Flutter web | активен |
| Remi/PyWebIO | нет | нет | да | голый HTML | голый HTML | лёгкие | вялые |
| Рукописный клиент (сейчас) | нет | нет | да | да, уже проверено | да | ноль | наш |

## Рекомендация

**Оставить рукописный клиент без каркаса и делить с GTK модели страниц, а не
виджеты.** Ни один кандидат не уменьшает число реализаций экрана, не ломая
при этом какое-то из уже выполненных требований (несколько браузеров, свой
плеер через медиапрокси, подключение по коду, лёгкость на ARM).

Основа уже есть: `Application.query(view, params, device)` в
`haltia_video/core/application.py` отдаёт оба интерфейса (`home`, `feed`,
`channel`, `playlist`, `bookmarks`, `search`…), а последний коммит сделал
Главную и «Мои плейлисты» общими. Дальше — довести это до того, чтобы
интерфейсам оставалось только рисовать:

1. **Модель страницы в ядре.** Каждый `query` возвращает не только данные, но
   и структуру: разделы с заголовками, карточки с типом и состоянием (☆,
   прогресс, «Продолжить»), список доступных действий меню карточки
   (`card_menu`: канал, подписка, закладка — с id команды и признаком
   «спросить подтверждение»), строку-заметку («Ищу…», «Ничего не найдено.» —
   как уже делает `search.view`). Тогда GTK и JS не решают, *что* показать,
   а только *как*.
2. **Один словарь строк.** Русские подписи кнопок и меню — в одном JSON/модуле
   ядра, который GTK читает напрямую, а клиент получает в `state`. Исчезает
   класс расхождений «в GTK назвали иначе».
3. **Обобщённые отрисовщики.** В обоих адаптерах — небольшой набор
   компонентов (сетка карточек, строка раздела с «Все →», меню действий,
   переключатель вкладок/чипов, список настроек) по модели из п. 1. Новая
   страница ядра появляется в обоих интерфейсах почти без кода; особые
   экраны (редактор порядка плейлиста, вход, плеер) остаются рукописными.
4. **Проверка паритета.** Тест, который для каждого `view` прогоняет одну
   модель через GTK-отрисовщик (без дисплея — на уровне построенного дерева
   описаний) и через веб (уже есть Playwright: `tests/test_web_ui`), и сравнивает
   набор разделов/действий. Это ловит «в вебе забыли кнопку».

**Стоимость (оценка, не измерение).** Модели страниц для уже общих `view` и
словарь строк — порядка 1–2 недель одного разработчика, в основном перенос
решений из `haltia-video` (≈3100 строк) в ядро с тестами, без смены форматов
данных. Обобщённые отрисовщики — ещё около недели на каждую сторону, причём
`app.js` (≈350 плотных строк) уже устроен как набор помощников (`grid`,
`entityRow`, `entityMenu`). Тест паритета — 2–3 дня. Новых зависимостей ноль,
B8000-H ничего не получает. После этого цена нового экрана — модель в ядре
плюс, если нужен новый тип компонента, по одному небольшому отрисовщику.

**Чего не делать.** Не строить веб-режим на Broadway (один браузер, нет видео,
уходит из GTK); не переписывать GTK-окно на Toga ради прототипного веба; не
заменять aiohttp-сервер NiceGUI/Flet — это сменит язык второго UI, но не
избавит от него, и принесёт FastAPI/uvicorn/Flutter на ПК и ARM.

Если когда-нибудь всё же понадобится «посмотреть само GTK-окно с телефона»
для отладки, Broadway доступен без установки (`broadwayd` есть в `gtk+3.0`),
но только за нашим прокси с подключением по коду и без плеера.
