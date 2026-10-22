# haltia-video

«Видео» (`io.github.haltia_de.Video`): видео с YouTube и VK Видео,
поиск, подписки на каналы, история. Сделано для планшета Lenovo YOGA Tablet 10 (B8000-H) с
оболочкой Haltia ([device-lenovo-b8000h](https://github.com/haltia-de/device-lenovo-b8000h)),
но это обычная программа GTK 3: работает и в GNOME, и в KDE. Видео
открывается на весь экран в `haltia-vplay` из этого же репозитория (`vplay/`:
libmpv, кадры масштабирует видеокарта), а где его нет — в `mpv`.

- **Главная** (открывается первой) — новое в подписках, затем популярное в
  VK Видео и, после входа в YouTube, его рекомендации.
- **Подписки** — новые видео каналов, на которые вы подписаны, вместе.
- **Каналы** — сами каналы с аватаром, `@именем`, числом подписчиков,
  датой нового видео и описанием: открыть, убрать. Поле над ними ищет
  новые каналы по названию, ссылке или `@имени`.
- **История** — что вы смотрели.
- Касание карточки — смотреть; долгое нажатие — подписаться на канал или
  отписаться.
- Видео продолжается с того места, где его оставили; полоска под картинкой
  показывает, сколько досмотрено. Внизу окна — панель того, что открыто в
  проигрывателе: «▶ Смотреть» возвращает к нему, ползунок выбирает место.

Поиск идёт сразу в YouTube и VK Видео; кнопка слева от поля («Везде» /
«YouTube» / «VK Видео») оставляет один источник. Результаты — с датой и
сначала новые, все источники вместе; кнопка над ними переключает на
«сначала подходящие», по источникам, YouTube первым.
Ссылка на канал сама находит его там, откуда она. Подписки и история —
общие для всех источников.

**Настройки** (⚙) включают и выключают источники и входят в аккаунты:

- VK Видео — по QR-коду или ссылке, подтверждённой на телефоне, как вход на
  телевизоре. Если VK показывает на телефоне одноразовый код, введите его
  в появившемся поле в Видео и нажмите «Подтвердить». Если поле ещё не
  появилось, нажмите «VK показал код». Неверный код можно ввести повторно;
  «Получить новый QR-код» начинает вход заново.
- YouTube сам войти программе не даёт, поэтому вход берётся из браузера,
  где вы вошли в аккаунт. Если на компьютере есть Firefox или Chromium (и
  Видео стоит не из Flatpak), достаточно нажать «Войти через Firefox».
  Иначе — на планшете и во Flatpak — нужен файл cookies (например, из
  расширения «Get cookies.txt LOCALLY»), положенный в
  `~/.config/haltia-video/youtube-cookies.txt`; кнопка «Проверить файл»
  проверяет его. С ним на Главной — рекомендации, а поиск и
  просмотр идут от вашего аккаунта.

После входа кнопка «Подписаться на каналы аккаунта» добавляет его каналы к
подпискам приложения.

Закрытое сообщество VK может не находиться по названию. Во вкладке
«Каналы» вставьте его ссылку в поле «Найти канал, ссылка или @имя» и
нажмите «Подписаться» у результата, либо импортируйте каналы аккаунта
через настройки. Вход в аккаунт сам по себе не добавляет его каналы.

Короткие вертикальные видео — Shorts YouTube и клипы VK — не показываются;
переключатель «Shorts и клипы» в настройках включает их в поиске, на
Главной, в подписках и на каналах.

Источники — модули в `sources/`: у каждого `search`, `channel_videos`,
`search_channels`, `find_channel`, `watch_url`, по желанию `home` и вход
(описание — в `sources/__init__.py`). Сейчас YouTube (поиск через yt-dlp,
лента канала — его открытый Atom-фид) и VK Видео (VK API с анонимным
ключом, как у самого vkvideo.ru, или с вашим после входа); Vimeo и другие
добавляются так же, файлом в `sources/`.

## Установка

**Ubuntu 24.04 и 26.04, Debian 13** (и elementary OS 8, Linux Mint 22;
только amd64) — из репозитория apt Haltia:

```
sudo curl -fsSLo /usr/share/keyrings/haltia-archive-keyring.gpg https://haltia-de.github.io/packages/apt/haltia-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/haltia-archive-keyring.gpg] https://haltia-de.github.io/packages/apt $(. /etc/os-release; echo $VERSION_CODENAME) main" | sudo tee /etc/apt/sources.list.d/haltia.list
sudo apt update && sudo apt install haltia-video
```

mpv, Pillow, qrcode и libsecret пакет берёт из дистрибутива, а свои yt-dlp
и QuickJS везёт в `/usr/lib/haltia-video/vendor`: в дистрибутивах они
слишком старые для YouTube.

**Flatpak** (любой дистрибутив; среда GNOME — с Flathub):

```
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --if-not-exists haltia https://haltia-de.github.io/packages/flatpak/haltia.flatpakrepo
flatpak install haltia io.github.haltia_de.Video
```

В Flatpak всё лежит в `~/.var/app/io.github.haltia_de.Video/`: подписки и
настройки — в `config/haltia-video/video.json`, история — в
`data/haltia-video/`, кэш — в `cache/haltia-video/`. Файл cookies YouTube
кладётся в `~/.var/app/io.github.haltia_de.Video/config/haltia-video/youtube-cookies.txt`
(вход через Firefox песочница не видит), вход хранится в связке ключей
через портал.

**Alpine и postmarketOS** — пакет `haltia-video` из
[репозитория apk Haltia](https://github.com/haltia-de/packages), для
armv7, aarch64 и x86_64, вместе с `haltia-vplay`.

**Из исходников:** нужны Python 3 с PyGObject (GTK 3),
[haltia-kit](https://github.com/haltia-de/kit), yt-dlp с
JavaScript-движком QuickJS, Pillow (картинки карточек) и `mpv` (или
`haltia-vplay`); для связки ключей — libsecret; для QR-кода входа —
модуль `qrcode` (без него показываются ссылка и код).

```
sh install.sh            # в /usr/local (нужен root)
sh install.sh /mnt/root  # в другой корень, например при сборке образа
```

`install.sh [ROOT] [PREFIX]` кладёт `.desktop`-файл, значок и metainfo, а
`haltia-vplay` — если он собран (`vplay/build.sh` собирает его для
планшета, пакет Alpine — сам). Сборка пакетов: `packaging/alpine/`,
`debian/` и `packaging/flatpak/`; выпуск по тегу собирает все три через
`haltia-de/ci`.

**На обычном компьютере** (вне оболочки Haltia) у окна обычные кнопки
рабочего стола, и «Закрыть» среди них, а видео играет `mpv`;
`haltia-vplay` — проигрыватель планшета, в deb и Flatpak его нет.

## Где что хранится

Вход в VK и файл cookies YouTube хранятся в связке ключей системы
(Secret Service; во Flatpak — через портал), а где её нет, как на
планшете, — файлами в `~/.config/haltia-video/`, доступными только вам.
Подписки и настройки — `~/.config/haltia-video/video.json`, история —
`~/.local/share/haltia-video/history.json`. В `~/.cache/haltia-video/` — картинки
(не дольше месяца и не больше 150 МБ), последняя лента и Главная: при
запуске они показываются сразу, а свежие подгружаются за ними.

## До 2.0

До 2.0 программа называлась b8k-video (`io.github.trudenboy.Video`). При
первом запуске 2.0 подписки, настройки, вход, история и кэш переезжают
из `~/.config/b8k` и папок `b8k-video` на новые места, а на старых
остаются ссылки. Команды `b8k-video` и `b8k-vplay` остаются ссылками на
новые до 3.0.

## Лицензия

GPL-2.0.
