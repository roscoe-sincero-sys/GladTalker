# GladTalker Studio

**GladTalker** is a fork of [OpenTalker/SadTalker](https://github.com/OpenTalker/SadTalker), licensed under the [Apache License 2.0](LICENSE). All original copyright and license notices are retained. Changes made in this fork are noted in the commit history.

---

**GladTalker Studio** turns a picture and a written script into a talking video—right on your own computer. You can animate a single portrait, a video-call screenshot with several people, or a comic panel, give each character their own voice, and create closed captions automatically. Everything runs locally on your machine, so your pictures and scripts stay private.

---

## Before You Start

You don't need any programming experience to use GladTalker Studio. Here is what your computer needs:

- **Windows, macOS, or Linux** — A dedicated graphics card (NVIDIA, AMD, or Apple Silicon) makes videos much faster, though a regular computer works too.
- **About 5 GB of free disk space** — For the program and its voice and face files (downloaded once).
- **Two free helper programs:**
  - **Python** — runs the GladTalker engine behind the scenes.
  - **FFmpeg** — combines the voices and animation into a finished video file.

---

## Installing GladTalker Studio

Follow these steps once to set up GladTalker on your computer.

### Step 1: Download GladTalker

1. On this GitHub page, click the green **Code** button near the top, then click **Download ZIP**.
2. Find the downloaded `.zip` file on your computer and extract (unzip) it somewhere easy to remember—for example, `C:\GladTalker` on Windows or `~/GladTalker` on macOS/Linux.

*(If you already have Git installed, you can run `git clone https://github.com/roscoe-sincero-sys/GladTalker.git` instead.)*

### Step 2: Install Python

- **Windows (most computers):** Download **Python 3.12** from [python.org/downloads](https://www.python.org/downloads/).
  > **Important:** On the first screen of the installer, check the box that says **"Add python.exe to PATH"** before clicking **Install Now**.
  *(Using Python 3.12 works on all Windows computers, including ones with AMD graphics cards.)*
- **macOS:** Download Python 3.12+ from [python.org/downloads](https://www.python.org/downloads/) or install it with Homebrew (`brew install python`).
- **Linux:** Python 3 is usually already installed. Make sure the `python3-venv` package is installed as well (for example, `sudo apt install python3-venv`).

### Step 3: Install FFmpeg

Open your computer's command window (**Terminal** or **PowerShell** on Windows; **Terminal** on macOS/Linux) and run the command for your system:

- **Windows (PowerShell):**
  ```powershell
  winget install Gyan.FFmpeg
  ```
- **macOS (Terminal, using [Homebrew](https://brew.sh/)):**
  ```bash
  brew install ffmpeg
  ```
- **Linux (Ubuntu / Debian):**
  ```bash
  sudo apt install ffmpeg
  ```

> **Tip:** After installing Python or FFmpeg, **close and reopen** your terminal window so your computer recognizes the new tools.

### Step 4: Set Up GladTalker's Files

Next, you'll create a small private folder (called a *virtual environment*) inside your GladTalker folder so GladTalker's tools stay tidy and don't affect anything else on your computer.

Open a terminal window, move into your GladTalker folder (for example, `cd C:\GladTalker`), and run **one** of the sets of commands below depending on your computer:

#### Windows — Standard (NVIDIA graphics card or no separate graphics card)

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install torch torchvision torchaudio
pip install -r requirements.txt
```

*(If you have an NVIDIA graphics card and want faster video creation, visit [pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/) to copy the CUDA version of the `pip install torch ...` line.)*

#### Windows — AMD Graphics Card (Radeon)

```powershell
py -3.12 -m venv venv-dml
.\venv-dml\Scripts\activate
pip install torch-directml
pip install -r requirements.txt
```

#### macOS and Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install torch torchvision torchaudio
pip install -r requirements.txt
```

This step downloads GladTalker's helper libraries and can take a few minutes. You only need to do it once.

### Step 5: Download the Voice and Face Models

You don't need to download any AI model files by hand. The first time you open GladTalker Studio, a **Setup** panel will appear at the top of the screen with a **Download models** button. Click it once to download the required files (about 2.5 GB) with a live progress bar.

---

## Running the Studio (Quick Start)

### Option 1: Open in Your Web Browser (Easiest)

1. Open a terminal window in your GladTalker folder.
2. Turn on GladTalker's environment:
   - **Windows (standard):** `.\venv\Scripts\activate`
   - **Windows (AMD):** `.\venv-dml\Scripts\activate`
   - **macOS / Linux:** `source venv/bin/activate`
3. Start the Studio:
   ```bash
   python studio_server.py --open
   ```

After a moment, GladTalker Studio will open automatically in your web browser at `http://127.0.0.1:7860`.

> **Keep the terminal window open** while you are using GladTalker Studio—that window is running the engine in the background. When you're finished, simply close the browser tab and close the terminal window (or press `Ctrl+C` in it).

### Option 2: Use the Desktop App (Windows)

GladTalker Studio is also available as a standalone desktop app that starts and stops the engine for you automatically—no terminal needed once it's installed. It also adds a normal **Save** window for projects, a **Show the video in its folder** button, a progress bar on the taskbar icon, and a desktop notification when a video finishes while you're working in another window.

The first time you open the desktop app, if it doesn't spot your GladTalker folder right away, click **Choose GladTalker folder…** and point it to the folder from Step 1. To build the desktop app yourself, see [studio-ui/README.md](studio-ui/README.md#desktop-app).

---

## Making Your First Video (3 Simple Steps)

Once GladTalker Studio is open, everything happens in three steps across the top of the screen:

### 1. Cast — Choose Who Speaks
- Upload a picture (a photo, a video-call screenshot, or a drawn/illustrated scene).
- Click **Auto-Detect Cast** to find every face automatically, or click **+ Add region** to place a box around a character yourself.
- Give each character a **Name**, pick a **Voice** (or fine-tune the pitch), and click **▶ Preview voice** to hear how they sound.
- You can adjust any character's box by dragging it, using the **Arrow** keys (**Alt + Arrow** to resize), typing X/Y/W/H numbers, or clicking **Snap to face** / **Expand to tile**.

### 2. Script — Write What They Say
- Paste a whole conversation using `Name: line` format (for example, `Alex: Welcome to the meeting!`) and click **Import**, or add lines one by one.
- If a word might be pronounced strangely by the voice, GladTalker shows a tip with a one-click **Auto-Fix** button.
- Choose **Natural pace** or **Fit a target length**, then click **▶ Preview full audio mix** to hear the entire soundtrack and check the timing before making the video.
- **Create closed captions** is turned on by default so your video includes `.srt` and `.vtt` caption files.

### 3. Roll — Make Your Video
- Check the **Ready to render?** list—if anything is missing, click its **Fix** button to jump straight to that field.
- Choose your video shape (16:9 HD, Full HD, 9:16 Vertical for phones, 1:1 Square, or Match source) and quality:
  - **Standard** — recommended for most videos (much faster).
  - **Enhanced faces** — extra face sharpening (sharper, but roughly 3× slower).
- Click **🎬 Render video**. Watch live progress by stage (or click **Cancel** anytime).
- When it finishes, watch your captioned video right in the Studio, read the transcript, and download the MP4 and caption files.

> **Saving your work:** GladTalker Studio saves your progress automatically so you won't lose it if you reload or close the window. Use **Save project** and **Open…** in the top bar anytime you want to keep a copy of a project as a `.gladtalker.json` file.

---

## Built for Accessibility

GladTalker Studio is designed to be usable by everyone:
- **Keyboard-friendly:** Every button, box, and script line works without a mouse or dragging.
- **Screen-reader-friendly:** Clear labels, headings, landmarks, and live status announcements.
- **Comfortable to read:** Follows your computer's light or dark theme, supports Windows High Contrast, respects reduced-motion settings, and reflows cleanly when zoomed in (up to 400%).
- **Consistent Help:** Click **Help** in the top-right corner at any time for quick tips and keyboard shortcuts.

For details on our accessibility testing, see [docs/accessibility/wcag22-audit.md](docs/accessibility/wcag22-audit.md).

---

## Common Questions & Troubleshooting

- **"FFmpeg not found" appears in Setup:**
  Install FFmpeg (Step 3 above), close your terminal window completely, open a new one, and start GladTalker Studio again.
- **The app takes a minute to start the first time:**
  This is normal—the computer is loading the AI libraries for the first time. Later starts are faster.
- **"No face was found" in a character's box:**
  Click **Expand to tile** or make the box a bit bigger so the character's whole head and shoulders fit inside. For drawn or cartoon characters, set **Detection sensitivity** to **Sensitive** or **Very sensitive**.
- **Making a video feels slow:**
  Switch Quality from **Enhanced faces** to **Standard**. Each character is only animated while they are speaking, so shorter scripts finish much faster.
- **Where are my finished videos saved?**
  Inside your GladTalker folder, open `results` → `studio` → `renders`. Each video has its own dated folder with the `.mp4` video and `.srt` / `.vtt` caption files.

---

## For Developers & Advanced Users

- **Studio UI & Desktop App source:** [studio-ui/README.md](studio-ui/README.md)
- **Local Engine API reference:** [docs/studio_api.md](docs/studio_api.md)
- **Extra installation notes (ROCm, WSL, Docker):** [docs/install.md](docs/install.md)
- **Release notes:** [CHANGELOG.md](CHANGELOG.md)

## License & Credits

GladTalker Studio is released under the [Apache License 2.0](LICENSE). It is built on [OpenTalker/SadTalker](https://github.com/OpenTalker/SadTalker), with voices powered by [Coqui TTS](https://github.com/idiap/coqui-tts) and optional face restoration by [GFPGAN](https://github.com/TencentARC/GFPGAN).
