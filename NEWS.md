# Что нового · What's new

## 2.8.0 (2026-10-27)

### Русский

Экран стал проще. Вкладки, поиск и настройки теперь в одной строке наверху, поиск открывается кнопкой с лупой, и под видео остаётся больше места. Вкладка «Подписки» называется «Новое», «Источники» — «Сервисы».

На картинке каждого видео появились звёздочка для закладки и кнопка «⋮»: она, как и долгое нажатие, открывает канал, подписку и закладку. В плейлисте над видео одна строка — порядок и крупная кнопка «Продолжить»; изменение порядка и сбросы перенесены в «⋮».

Отписка и удаление закладки спрашивают подтверждение; в настройках это можно выключить, тогда внизу появится кнопка «Вернуть». Подсказка о входе в YouTube свёрнута, доступ из локальной сети перенесён в раздел «Для разработчика». На вертикально повёрнутом планшете видео идут в две колонки.

### English

The screen is simpler. Tabs, search and settings now share one row at the top, search opens from the magnifier button, and the videos get more room. The «Подписки» tab is now «Новое», and «Источники» are «Сервисы».

Every video picture now carries a bookmark star and a «⋮» button which, like a long press, offers the channel, following and the bookmark. A playlist has one row above its videos, the order and a large «Продолжить» button; editing the order and the resets moved into «⋮».

Unfollowing a channel and removing a bookmark ask first; this can be turned off in Settings, and then a «Вернуть» button appears instead. The YouTube sign-in hint is folded, and LAN access moved into a «Для разработчика» section. On an upright tablet the videos show in two columns.

## 2.7.1 (2026-10-27)

### Русский

Видео больше не закрывается само при смене сортировки плейлиста и фильтров закладок: на планшете это иногда роняло программу.

### English

Video no longer closes by itself when a playlist's sort order or the bookmark filters change: on the tablet this sometimes crashed the program.

## 2.7.0 (2026-10-27)

### Русский

На планшете плейлист теперь играет как очередь: в проигрывателе рядом с кнопками перемотки появились «предыдущее» и «следующее» видео, а после окончания ролика сразу начинается следующий в выбранном порядке. Заголовок меняется вместе с роликом, окно закрывается в конце плейлиста. Закрытые видео VK по-прежнему открываются через вход в аккаунт, без передачи токена проигрывателю.

### English

On the tablet a playlist now plays as a queue: the player shows previous and next video buttons beside the seek buttons, and the next video in the chosen order starts when one ends. The title follows each video, and the window closes at the end of the playlist. Private VK videos still open through the signed-in account without handing its token to the player.

## 2.6.0 (2026-10-26)

### Русский

Плейлисты получили локальный «Мой порядок» и редактор с перестановками через страницы, сохранением и отменой. В сортировке по урокам можно менять порядок внутри одинакового номера и вручную отмечать задания выполненными независимо от просмотра. «Продолжить» пропускает выполненные задания; прогресс, выполнение и ручной порядок сбрасываются отдельно. Правки общие для GTK и веб, оригинал в сервисе сохраняется. Сохранение порядка обновляет очередь, сохраняя текущий ролик и позицию.

Добавлена необязательная публикация веб-интерфейса через nginx: в настройках ПК можно указать внешний адрес и IP доверенного прокси. Ссылка и QR-код используют этот адрес, подключение браузеров по коду сохраняется; внешний HTTPS поддерживается с завершением TLS в прокси. По умолчанию публикация выключена.

На ПК появился встроенный веб-интерфейс для планшета в локальной сети. Включите доступ в настройках, подключите браузер одноразовым кодом или QR-кодом; подключённые устройства можно отозвать там же. Поиск, каналы, плейлисты, закладки и прогресс используют общую библиотеку с GTK. Веб-плеер передаёт видео через ПК, поддерживает перемотку и полноэкранный режим; раздельные дорожки объединяются FFmpeg без перекодирования. Доступ выключен по умолчанию и работает, пока открыто приложение на ПК. Проверка на реальном Lenovo P11 ещё требуется.

### English

Playlists now offer a local custom order and a paged editor with save and cancel. Lesson order allows rearrangement within equal lesson numbers and manual task completion independently of watching. Resume skips completed tasks; playback progress, task completion and manual order have separate resets. Edits are shared by GTK and the web client without changing the service playlist. Saving an order updates playback queues while preserving the current video and position.

Optional nginx publication now accepts an external URL and trusted proxy IPs in PC settings. Pairing links and QR codes use that URL, browser code pairing remains required, and external HTTPS works with TLS termination at the proxy. Publication is off by default.

PC installations now offer an embedded web interface for tablets on the local network. Enable access in Settings and pair a browser with a one-time code or QR code; paired devices can be revoked there. Search, channels, playlists, bookmarks and progress share one library with GTK. The web player streams through the PC, supports seeking and fullscreen, and combines separate tracks using FFmpeg streamcopy. Access is off by default and lasts while the PC application is open. Real Lenovo P11 validation is still required.

## 2.5.0 (2026-10-26)

### Русский

Вкладка «Закладки» сохраняет видео, плейлисты и каналы независимо от подписок. Есть поиск, совместные фильтры по типу, сервису и синхронизации, сортировка и закрепление с ручным порядком. Добавить объект можно с карточки или по ссылке. Закладки доступны без сети; недоступность синхронизации при текущем способе входа VK показывается явно. YouTube пока сохраняется локально.

Плейлист запоминает последнее видео и выбранную сортировку. При открытии сразу виден блок продолжения с позицией просмотра; запуск и переход к ролику выполняются отдельными кнопками. «Показать в списке» прокручивает список до нужной карточки, выделяет её и передаёт ей фокус. Прогресс сохраняется между запусками независимо от ограничения истории. После завершённого ролика предлагается следующий в выбранном порядке; досмотренным считается естественное окончание или последние 10 секунд. Кнопка «Сбросить прогресс» появляется только в блоке продолжения рядом с его действиями и очищает точку продолжения и позиции видео плейлиста после подтверждения; сортировка, закладки и история сохраняются.

На странице канала VK Видео появились плейлисты. Откройте плейлист, чтобы смотреть его видео в заданном порядке; кнопки «Следующие» и «Предыдущие» позволяют просмотреть весь список.

В плейлисте можно выбрать «Порядок VK», «Обратный порядок» или «По номеру урока» (урок 2 перед уроком 10). Обратный порядок и номера учитывают весь плейлист, а не только открытую страницу. Режим по номеру распознаёт также дни; внутри одинакового номера применяется обратная последовательность VK.

На компьютере кнопки следующего и предыдущего видео в mpv теперь переключают ролики внутри открытого плейлиста в выбранном порядке. История и позиция просмотра сохраняются для каждого ролика.

Вход в VK теперь используется и при запуске видео в проигрывателе на компьютере: видео закрытых сообществ открываются через авторизованный поток.

Вход в VK Видео по QR-коду теперь позволяет ввести одноразовый код, который VK показывает на телефоне. Неверный код можно ввести повторно без нового QR-кода; ошибка связи не сбрасывает попытку ввода.

Исправлен импорт каналов аккаунта VK: сообщества с видео, в том числе закрытые, больше не пропускаются из-за отсутствующих счётчиков в пакетном ответе VK.

### English

The Bookmarks tab saves videos, playlists and channels independently of followed channels. It offers search, combined type/source/sync filters, sorting and manually ordered pins. Save from a card or a link. Bookmarks work offline; unavailable VK bookmark sync is explained for the current sign-in method. YouTube bookmarks remain local.

Playlists remember their last played video and chosen order. Opening one immediately shows a resume block with playback position; playback and locating its video require separate buttons. Locating scrolls to, highlights and focuses the actual card. Progress survives restarts and history eviction. Completed videos offer the next video in the selected order; completion means a natural end or at most ten seconds remaining. Reset progress appears only alongside the resume actions and clears the playlist cursor and its videos' positions after confirmation, keeping sorting, bookmarks and history.

VK Video channel pages now list their playlists. Open a playlist to browse its videos in playlist order, with next and previous pages for longer lists.

Playlists offer VK order, reverse order and natural lesson-number order (lesson 2 before lesson 10). Reverse and lesson ordering cover the entire playlist, across all pages. Lesson order also recognizes day numbers and uses reverse VK sequence within equal numbers.

On the desktop, mpv next and previous controls now move through the opened playlist in its selected order. History and playback position are tracked per video.

The desktop player now uses the signed-in VK account to open private community videos through an authenticated stream.

Signing in to VK Video by QR code now accepts the one-time code VK shows on the phone. An incorrect code can be retried without a new QR code; a connection error keeps the code entry available.

Importing VK account channels no longer skips communities with videos, including private ones, because VK omits their counters in a multi-community response.

## 2.4.3 (2026-10-22)

### Русский

Вверху слева на начальном экране теперь кнопка «домой», а на вложенных экранах — «←». Запуск, перемотка и смена формата в проигрывателе стали плавнее; миниатюры занимают меньше памяти.

### English

The top left button now shows home on the start screen and “←” on deeper screens. Starting playback, seeking and changing formats are smoother; thumbnails use less memory.

## 2.4.2 (2026-10-21)

### Русский

Повторное нажатие до запуска проигрывателя или закрытие Видео больше не оставляет проигрыватель работать незаметно. Версия 2.4.1 не вышла.

### English

Tapping again before the player has started or closing Video no longer leaves a player running unseen. Version 2.4.1 was not released.

## 2.4.1 (2026-10-21)

### Русский

Повторное нажатие до запуска проигрывателя или закрытие Видео больше не оставляет проигрыватель работать незаметно.

### English

Tapping again before the player has started or closing Video no longer leaves a player running unseen.

## 2.4.0 (2026-10-20)

### Русский

Если связка ключей закрыта, в настройках об этом сказано и есть кнопка «Открыть связку ключей»; вход в аккаунт не теряется, а видео пока открываются как для гостя.

### English

When the keyring stays locked, Settings says so and offers to open it; the account is kept, and videos play as a guest meanwhile.

## 2.3.1 (2026-10-20)

### Русский

На карточках новых видео каналов YouTube снова видна длительность.

### English

Cards of YouTube channels' new videos show how long each video is.

## 2.3.0 (2026-10-20)

### Русский

Короткие вертикальные видео — Shorts YouTube и клипы VK — больше не показываются. Переключатель «Shorts и клипы» в настройках возвращает их в поиск, на Главную, в подписки и на каналы.

### English

Short vertical videos — YouTube's Shorts and VK's clips — are hidden now. The "Shorts и клипы" switch in Settings brings them back to search, Home, Subscriptions and channels.

## 2.2.0 (2026-10-20)

### Русский

На планшете видео начинается намного быстрее: Видео само находит поток, а не запускает для каждого новый yt-dlp, и пропускает субтитры, которых не показывает. Снова открываются видео VK с субтитрами.

### English

Videos start much sooner on the tablet: Video finds the stream itself instead of starting a new yt-dlp for each one, and skips the subtitles it does not show. VK videos with subtitles play again.

## 2.1.2 (2026-10-19)

### Русский

Вне оболочки Haltia у окна есть обычные кнопки рабочего стола, и среди них — «Закрыть».

### English

Outside the Haltia shell the window has the desktop's usual buttons, close among them.

## 2.1.1 (2026-10-19)

### Русский

В выпуске теперь есть и пакеты для Debian, Ubuntu и Flatpak. На обычном компьютере видео с YouTube снова открываются в mpv.

### English

The release also carries packages for Debian, Ubuntu and Flatpak. On a desktop, YouTube videos open in mpv again.

## 2.1.0 (2026-10-19)

### Русский

Вход в YouTube одной кнопкой: Видео само берёт вход из Firefox или Chromium, выгружать cookies больше не нужно. Вход в VK и YouTube хранится в связке ключей системы, а не в открытом файле. На обычном компьютере видео открывается в mpv.

### English

Signing in to YouTube is one button: Video takes the sign-in from Firefox or Chromium, no cookies to export any more. The VK and YouTube sign-ins are kept in the system's keyring, not in a plain file. On a desktop, videos open in mpv.

## 2.0.1 (2026-10-19)

### Русский

Новая иконка в стиле Haltia. Видео теперь ставится и на 64-битные устройства с postmarketOS.

### English

A new icon in Haltia's style. Video now installs on 64-bit devices with postmarketOS too.

## 2.0.0 (2026-10-19)

### Русский

Программа теперь часть Haltia. Подписки, история, настройки и вход в аккаунты переезжают сами при первом запуске.

### English

The program is part of Haltia now. Followed channels, history, settings and sign-ins move over by themselves at the first start.

## 1.12.6 (2026-10-19)

## 1.12.5 (2026-10-18)

## 1.12.4 (2026-10-18)

## 1.12.3 (2026-10-18)

## 1.12.2 (2026-10-18)

## 1.12.1 (2026-10-18)

## 1.12 (2026-10-18)
