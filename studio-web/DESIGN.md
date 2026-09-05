---
name: 服飾圖片工作室
description: Local photo-studio desk with actual output preview and editable free crop.
colors:
  canvas: "#eef1f4"
  surface: "#fff"
  ink: "#233044"
  muted: "#536278"
  accent: "#375a96"
  accent-soft: "#e8eef8"
  line: "#ccd3dd"
  crop: "#b94338"
typography:
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang TC", "Noto Sans TC", sans-serif'
    fontSize: "14px"
  headline:
    fontSize: "23px"
    fontWeight: 650
    lineHeight: 1.4
  title:
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.45
rounded:
  control: "7px"
  panel: "10px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "white"
    rounded: "{rounded.control}"
    padding: "8px 13px"
---

# Design System: 服飾圖片工作室

## Overview

**Creative North Star: "Local studio desk"**

A compact, neutral workbench: a cool-grey workspace holds white photo surfaces, blue actions and a blue crop frame. Photographs carry the content; controls explain selection, cropping and output without restyling the images. This record captures the implemented direction in `index.html`, `style.css` and `app.js`, not a new visual proposal.

**Key Characteristics:**

- Visible left/right selection and a large actual output preview
- Flat, bordered work areas with restrained state color
- Real local photographs, with no generated or remote image assets

## Colors

Action blue (`accent`) identifies primary actions, selected photo borders, left/right markers, active pair borders and the editable crop frame/handles. Pale blue (`accent-soft`) backs selected photos and active pairs. The cool-grey `canvas`, white `surface`, dark `ink`, secondary `muted` and separator `line` tokens form the neutral interface. Photographs receive no color treatment.

## Typography

One native system sans stack serves every role, with Traditional Chinese fallbacks and no downloaded fonts. Headline, title and body values are normative above; the headline becomes (19px) on mobile. Ordinary buttons use weight (550); paragraphs use line-height (1.7). Most secondary labels are (12px), supporting metadata is (11px), and thumbnail filenames become (10px) on mobile. Crop pixel counts and pair numbers use tabular numerals.

## Layout

The centered main container has maximum width (1580px) and desktop padding (20px 28px 0). The library and preview use `minmax(0, 1.12fr) minmax(360px, 1fr)` columns with a (24px) gap; panels have (20px) internal padding. The saved-pair tray sits below both areas, scrolls horizontally and uses fixed-width (128px) cards.

The contact sheet defaults to four columns, (14px) row gaps, (10px) column gaps and a (720px) scrolling height cap. Thumbnails use a (3:4) frame with `object-fit: contain`.

- At (1500px) and above: five contact-sheet columns
- At (1050px) and below: main and panel padding becomes (16px), workbench gap becomes (16px), preview column minimum becomes (350px), and contact-sheet columns become three
- At (760px) and below: work areas stack, panel padding becomes (14px), the contact sheet returns to four columns with a (440px) height cap, used-photo badges are hidden, and export results wrap
- Both crop editors remain side by side; their gap changes from (14px) to (12px) on mobile

The preview centers a contained image without forcing its aspect ratio. Image height is capped at (430px) on desktop and (460px) on mobile. Square output explicitly shows padding; natural-ratio output does not add canvas padding.

## Elevation & Depth

Work panels are flat and separated by borders and surface tones. Only the actual collage image has a small shadow (`0 3px 10px #23304413`). The crop frame is drawn directly over each source photo without adding a white background or changing the photo pixels. Loading reduces preview-stage opacity to (0.65).

## Shapes

Controls and panels use the frontmatter radii. Inputs and selects use (5px), photo frames and the preview stage use (6px), and selected photos have a (2px) border. No decorative gradients are used. The crop frame uses a thin blue border and circular edge/corner handles with a (24px) interaction target.

## Components

- **Buttons:** bordered white defaults, filled blue primary actions, transparent quiet actions and red destructive text. Ordinary controls are at least (38px) tall; small variants and mobile header actions are (30px). Background and border transitions take (0.16s); reduced-motion preferences disable transitions. Disabled controls use opacity (0.44)
- **Focus:** a (3px) blue outline with (3px) offset marks keyboard focus. Photo and saved-pair buttons retain focus by item identity when lists rebuild. This record is not a full accessibility or contrast certification
- **Photo modes:** the library defaults to `拼圖`; `單張裁切` opens one selected photo in the right editor and `刪除照片` is an explicit destructive mode. First and second studio selections are marked 左 and 右. A third choice prompts saving or deselection. Selected photos use `aria-pressed`; already-used photos remain available for other pairs. Swap changes sides; adding saves the pair; selecting a tray card opens it for editing and removal
- **Preview and tray:** the large preview shows actual output. The preview has a separate `選擇裁切` mode; handles appear only after activation, and `完成裁切` applies the selected output frame. Tray thumbnails identify the original photos, not their final crop or padding
- **Pair deletion:** each tray item has an always-visible (32px) circular SVG close button in its upper-right corner, separate from the edit button. It removes only that pair without confirmation, preserves other drafts and exports, and moves keyboard focus to a neighboring close button (or the photo picker when empty). Hover uses the existing crop-red token
- **Crop editor:** each side or a single selected photo has a source image, four draggable edge handles and four draggable corner handles with a pixel readout and a visible reset button. Single-photo apply updates the photo thumbnail and all uses of that photo; cancel discards the draft. Arrow keys move one source pixel, Shift moves ten, and Home/End move an edge to its allowed bound. It starts at the full source-photo bounds and never reads model crop suggestions
- **Fields and status:** native text and select controls label product name and output format. Import warnings expand separately. A polite live status region reports work and errors. Export is unavailable without saved pairs and reveals its local result after completion

## Do's and Don'ts

- **Do** preserve image proportions and distinguish free cropping from canvas padding
- **Do** keep selected photos, the active saved pair and keyboard focus identifiable
- **Do** use the actual large preview as the authority for the exported result
- **Do** preserve raster provenance from the user's 原圖 files and SHA-keyed local imports
- **Don't** read or apply an AI crop suggestion; the crop rectangle is the user's choice
- **Don't** treat source thumbnails as proof of final crop or padding
- **Don't** add decorative motion, remote imagery or unsubstantiated brand claims
