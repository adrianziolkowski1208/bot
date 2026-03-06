# Discord Music Bot (Python)

Prosty bot muzyczny na Discorda z komendami tekstowymi i slashowymi.

## Funkcje

- `!join` / `/join` – bot dołącza do Twojego kanału głosowego
- `!play <link lub fraza>` / `/play` – odtwarza muzykę z YouTube / SoundCloud oraz obsługuje linki Spotify i Apple Music (przez wyszukanie odpowiednika na YouTube)
- `!queue` / `/queue` – pokazuje aktualny utwór, kolejkę i tryb loop
- `!skip` / `/skip` – pomija aktualny utwór
- `!loop [off|track|queue]` / `/loop` – zapętlenie: wyłączone, piosenka albo cała playlista
- `!stop` / `/stop` – zatrzymuje odtwarzanie, czyści kolejkę, wyłącza loop i rozłącza bota
- `!leave` / `/leave` – rozłącza bota z kanału
- `/panel` – wysyła panel sterowania z przyciskami (play/skip/stop/queue/loop)
- wiadomości tekstowe na kanale sterowania (CONTROL_CHANNEL_ID) działają jak `play`

## Wymagania

- Python 3.10+
- `ffmpeg` zainstalowany w systemie

### Instalacja ffmpeg

- Ubuntu/Debian: `sudo apt install ffmpeg`
- macOS (Homebrew): `brew install ffmpeg`
- Windows: pobierz z https://ffmpeg.org/download.html i dodaj do `PATH`

## Instalacja

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Konfiguracja

1. Utwórz aplikację i bota na Discord Developer Portal.
2. Włącz **MESSAGE CONTENT INTENT** dla bota.
3. Zaproś bota na serwer z uprawnieniami do odczytu/pisania wiadomości, używania aplikacji komend (slash) i łączenia z voice.
4. (Opcjonalnie) utwórz kanał sterowania i ustaw jego ID jako `CONTROL_CHANNEL_ID`.
5. Skopiuj `.env.example` do `.env` i uzupełnij token:

```bash
cp .env.example .env
```

W `.env` ustaw:

```env
DISCORD_TOKEN=twoj_token
CONTROL_CHANNEL_ID=123456789012345678
```

## Uruchomienie

```bash
export $(grep -v '^#' .env | xargs)
python bot.py
```

## Uwagi

- Panel (`/panel`) zostanie wysłany na kanał `CONTROL_CHANNEL_ID` (jeśli ustawiony), w przeciwnym razie pojawi się w bieżącym kanale.
- Loop przełącza tryby: `off` → `track` (zapętlenie piosenki) → `queue` (zapętlenie playlisty).
- Wiadomości tekstowe wysyłane na kanał `CONTROL_CHANNEL_ID` są traktowane jako komenda `play`.
- Bot używa `yt-dlp` do pobierania źródła audio.
- Dla Spotify i Apple Music bot pobiera metadane utworu i wyszukuje odpowiadające audio na YouTube.
- Jeśli odtwarzanie nie działa, najczęściej brakuje `ffmpeg` albo bot nie ma uprawnień voice.
