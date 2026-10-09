# A6SiliconPatcher by TheHappyJolly

Runs the original 32-bit macOS version of **Asphalt 6: Adrenaline** (1.0.9) on Apple Silicon Macs, with a few extras.
You need **your own copy** of `Asphalt 6.app` — nothing of the game is in this repository or in the downloads; the patcher builds a
separate patched copy from your files.

Запускает оригинальную 32-битную macOS-версию **Asphalt 6: Adrenaline** (1.0.9) на Mac с Apple Silicon. Нужна **ваша собственная копия**
`Asphalt 6.app`: в репозитории и в загрузках нет ничего из игры, патчер собирает отдельную копию из ваших файлов.

## Install / Установка

Apple Silicon Mac (M1+), macOS 11+. Open Terminal and run / откройте Терминал и выполните:

```
bash -c "$(curl -fsSL https://archive.org/download/a6siliconpatcher/install.sh)"
```

Read it first / сначала прочитать скрипт: `curl -fsSLO https://archive.org/download/a6siliconpatcher/install.sh && bash install.sh`

Remove / удалить: add / добавьте `_ --uninstall` к команде. Your saves are kept / сохранения остаются.

The installer installs Rosetta 2 if needed (asks for the admin password), finds your `Asphalt 6.app`, downloads the engine (~40 MB),
builds `Asphalt 6 (Apple Silicon).app` in `/Applications` and checks it. / Установщик при необходимости ставит Rosetta 2, находит вашу
`Asphalt 6.app`, скачивает движок (~40 МБ), собирает приложение в `/Applications` и проверяет его.

## Extras / Дополнительно

* **3D Wheels in Race** (Options → Visuals) — 3D wheels during races.
* **Original Duel Difficulty** (Options → Gameplay, renamed from Controls) — Off: easier Duel opponents.
* Supersampled picture (anti-aliasing), native full screen, Apple Silicon friendly power use. `A6_SS=1` turns supersampling off.

## How it works / Как это устроено

`n6/` is a Python runtime (x86_64 Python under Rosetta 2) that hosts the game's 32-bit code and provides the system services it
expects (Cocoa, OpenGL, audio...). `native/` is the small 32-bit-mode host and launchers, `guestlib/` helper code for the guest,
`tools/` the Options menu patcher, `release/` the installer and the release builder (`build_release.sh`).

## Legal / Правовая информация

Not affiliated with Gameloft. "Asphalt" and "Gameloft" belong to their owners. No game code or data is distributed; the engine is
original work plus open-source components (see `release/LICENSES.txt`). Use only with a copy of the game you legally own.
