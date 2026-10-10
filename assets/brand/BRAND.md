# Read Aloud / Vorlesen: brand brief

This brief is for whoever wires the icons into the app and its installers. It explains what the mark is, how it is built, which file goes where, and what to leave alone.

## Concept

The mark is a speech bubble that talks. A rounded bubble draws its bottom left corner out into a short tail, and five sound bars of uneven height sit inside it. The bubble says "voice" and the bars say "audio". Together they show one idea, text that turns into speech, so the icon carries a single metaphor as the Windows and GNOME guidelines ask.

The mark grows out of the onboarding mascot and keeps its colours and its five bars. Three things changed. The tail now grows from the bubble's corner instead of hanging below it as a separate rotated square, so the silhouette is one shape that survives 16 px. The bars are now round-capped pills on a fixed rhythm. The sparkle stays with the mascot and is absent from the icon, because a second motif turns into noise below 32 px.

## Construction

The master is drawn on a 256 grid, which is the 32 px grid scaled by 8.

```
bubble body    x 16..240, y 32..196, corner radius 56
tail           left edge runs straight down to the tip at (16, 236);
               the diagonal meets the body's bottom edge at x = 80
bars           5 pills, width 18, centres x = 64, 96, 128, 160, 192
bar spans      y 89..139, 73..155, 58..170, 81..147, 92..136
front edge     the same bubble shifted 8 down, drawn behind in a darker violet
```

The front edge gives the icon the slight thickness that GNOME asks for and the soft depth Windows 11 icons have, without a blur filter. QtSvg and most icon pipelines rasterise it identically.

Every small size has its own drawing, aligned to whole pixels. Scaling the master down produces grey, blurry bars, so the small files are separate designs.

| File | Grid | Bars | Bar width | Notes |
|---|---|---|---|---|
| `app-icon.svg` | 256 | 5 | 18 | gradient, rose centre bar, front edge |
| `app-icon-32.svg` | 32 | 5 | 2 | flat violet, 1 px front edge |
| `app-icon-24.svg` | 24 | 3 | 3 | flat violet, 1 px front edge |
| `app-icon-16.svg` | 16 | 3 | 2 | flat violet, no front edge |
| `tray-*.svg` | 16 | 3 | 2 | one colour, bars cut out of the bubble |

On the 16 grid the body spans x 1..15 and y 2..12 with radius 3.5, the tail tip sits at (1, 15), and the bars occupy x 3..5, 7..9 and 11..13. Every vertical edge lands on a pixel boundary.

## Colours

All values come from `bin/ui/Theme.qml` or sit on the same violet ramp.

| Role | Hex | Used in |
|---|---|---|
| Primary violet | `#6750A4` | mark, small app icons, light tray accent |
| Gradient start | `#8A70D6` | master app icon, top left |
| Gradient end | `#553C96` | master app icon, bottom right |
| Front edge | `#3F2A78` | app icons |
| Rose (tertiary container) | `#FFD8E4` | master app icon, centre bar only |
| White | `#FFFFFF` | bars; dark tray glyph |
| Ink | `#1D1B20` | wordmark |
| Light tray glyph | `#1C1B1F` | `tray-light*.svg` |
| Light tray speaking | `#6750A4` | `tray-light-speaking.svg` |
| Light tray error | `#B3261E` | `tray-light-error.svg` |
| Dark tray speaking | `#A98DF7` | `tray-dark-speaking.svg` |
| Dark tray error | `#FF897D` | `tray-dark-error.svg` |

The gradient runs at about 120 degrees from top left to bottom right, which is the Windows default. The violet keeps a contrast ratio of about 6:1 on a light taskbar. On a dark taskbar the white bars carry the contrast.

## Files

```
logo-mark.svg          flat mark, violet bubble with white bars, 256 viewBox
logo-lockup-en.svg     mark plus "Read Aloud", text as paths
logo-lockup-de.svg     mark plus "Vorlesen", text as paths
app-icon.svg           master app icon, 256
app-icon-16/24/32.svg  pixel-aligned small app icons
tray-light*.svg        dark glyph for light taskbars and panels
tray-dark*.svg         light glyph for dark taskbars and panels
tray-symbolic*.svg     recolourable glyph for Plasma and GNOME
preview/               PNG renders and mockups, for review only
```

The wordmark is Nunito at weight 720 with a tracking of -6 units, outlined to paths. Nunito is licensed under the SIL Open Font License, which permits outlining it into a logo. The lockups therefore need no font at runtime. Cap height is half the bubble body, the caps are centred on the body, and the gap between mark and text is 56 units on the 256 grid. For a dark background, change the wordmark's `fill="#1D1B20"` to `#E6E0E9`; the mark stays as it is.

## Tray states

Each state swaps what is inside the bubble and leaves the silhouette alone. At 16 px a corner badge would have to cut into the right-hand bar, and a 6 px badge with a 1 px gap is barely readable on a 100 % display. A full glyph swap stays readable at 16 px.

| State | Inside the bubble | Colour |
|---|---|---|
| idle | three sound bars | glyph colour |
| speaking | taller, livelier bars | accent (violet, or Highlight on Plasma) |
| paused | pause symbol, two squared bars | glyph colour |
| error | exclamation mark | error red (NegativeText on Plasma) |

Speaking changes colour and bar shape at once, so the state still reads on a symbolic GNOME panel, where everything shares one colour.

## Platform rules

Windows. Pick `tray-light*` or `tray-dark*` from the taskbar theme, which is the registry value `SystemUsesLightTheme` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`. Do not read `AppsUseLightTheme`; the taskbar can be dark while apps are light. Listen for `WM_SETTINGCHANGE` and swap the icon live. The tray uses 16, 20, 24 and 32 px at 100, 125, 150 and 200 % scaling (Microsoft, "App icon construction").

Plasma. Plasma recolours an SVG icon at runtime when it carries a `<style id="current-color-scheme">` block and its shapes use a class such as `ColorScheme-Text` with `fill:currentColor`. The symbolic files follow that convention and also use `ColorScheme-Highlight` and `ColorScheme-NegativeText`. Recolouring happens only when Plasma loads the icon by name from an icon theme. A pixmap that the app sends over D-Bus stays as it is. Install the symbolic files into `~/.local/share/icons/hicolor/scalable/status/` under a unique name, for example `read-aloud-symbolic.svg` and `read-aloud-speaking-symbolic.svg`, and set the tray icon with `QIcon.fromTheme("read-aloud-symbolic")`. Then check with `dbus-monitor` that the StatusNotifierItem publishes `IconName` and not `IconPixmap`. If it sends a pixmap, fall back to `tray-light*` or `tray-dark*` chosen from the Plasma colour scheme.

GNOME. Install the same symbolic files. GTK recolours `-symbolic` icons with the foreground colour, which ignores the Highlight and NegativeText classes, so speaking and error then differ by shape only. That is enough, because each state has its own interior.

## Integration

App icon on Windows. Build the `.ico` from the hand-tuned files and do not downscale the master into the small slots:

```
16  <- app-icon-16.svg
20  <- app-icon-24.svg rendered at 20
24  <- app-icon-24.svg
32  <- app-icon-32.svg
40, 48, 64, 128, 256  <- app-icon.svg
```

App icon on Linux. Install `app-icon.svg` as `hicolor/scalable/apps/<app-id>.svg`, plus 16, 24 and 32 px PNGs from the matching small files in `hicolor/<n>x<n>/apps/`. Set the window icon with `setWindowIcon` in `tts_reader.py` and the settings window, replacing `audio-speakers`.

Tray. Replace `QIcon.fromTheme("audio-speakers")` in `bin/tts-tray.py` with the scheme above. Build one `QIcon` per state with 16, 20, 24 and 32 px pixmaps from the 16 grid file. Switch states from the daemon status: speaking while audio plays, paused while paused, error when the daemon does not answer.

Settings sidebar tile. Replace the 40 px `Rectangle` with the `ic.app` glyph in `bin/tts_settings.qml` with `logo-mark.svg` shown at 40 px. Drop the tile background, because the bubble is its own shape. If the layout needs a square slot, keep a transparent 40 by 40 item and centre the mark in it.

Onboarding mascot. Keep `Mascot.qml` animated: the glow, the bouncing bars, the hop, the waves and the sparkle all stay. Change its body to the new proportions:

```
bubble          width = size, body height = size * 0.73, corner radius = body height * 0.34
tail            drawn from the bottom left corner as part of one Shape path,
                tip below the left edge at body height * 0.24, diagonal meets
                the bottom edge at size * 0.29 from the left
bars            5 pills, width = size * 0.08, centre spacing = size * 0.143
bar rest height size * (0.22, 0.37, 0.50, 0.29, 0.20)
colours         bubble primaryContainer, bars primary (unchanged)
```

Port the bubble as a `QtQuick.Shapes` path using the coordinates in "Construction", scaled by `size / 224`. The bars keep their animation and only change their rest heights and spacing.

## Do

- Use the small files for 16, 24 and 32 px.
- Keep at least a quarter of the bubble height as clear space around the mark.
- Keep the lockup text as paths. If the wordmark has to be live text in the UI, set it in Nunito 720 or fall back to the system UI font, which is what the settings window already does.
- Keep the tail on the left. The bubble speaks to the right, towards the text.

## Do not

- Do not scale `app-icon.svg` below 40 px.
- Do not put the mark on a coloured tile, add an outline or add a shadow.
- Do not recolour the bars, apart from the rose centre bar in the master.
- Do not add the sparkle, waves or text to the app icon or the tray glyphs.
- Do not mirror the bubble or rotate it.

## Minimum sizes

```
app icon      16 px (app-icon-16.svg)
tray glyph    16 px
logo mark     16 px
lockup        120 px wide; below that, use the mark alone
```

## Sources

- Microsoft, "Design guidelines for Windows app icons": learn.microsoft.com/windows/apps/design/style/iconography/app-icon-design
- Microsoft, "App icon construction" (size table per scale factor): learn.microsoft.com/windows/apps/design/style/iconography/app-icon-construction
- GNOME Human Interface Guidelines, "App icons": developer.gnome.org/hig/guidelines/app-icons.html
- Marco Martin, "Icon colors" (Plasma 5.7 stylesheet recolouring): notmart.org/blog/tag/breeze
- KDE Frameworks, KIconColors API (list of ColorScheme classes): api.kde.org/frameworks/kiconthemes/html/kiconcolors_8h_source.html
