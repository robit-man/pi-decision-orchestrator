---
version: alpha
colors:
  primary: "#315fe9"
  canvas: "#f4f7fb"
  surface: "#ffffff"
  ink: "#17243b"
  muted: "#55657b"
  success: "#087762"
  warning: "#93610b"
  danger: "#b6384a"
  border: "#dce3ed"
typography:
  display:
    fontFamily: "IBM Plex Sans, sans-serif"
  body:
    fontFamily: "Public Sans, sans-serif"
  data:
    fontFamily: "IBM Plex Mono, monospace"
rounded:
  control: "7px"
  panel: "12px"
spacing:
  compact: "8px"
  control: "12px"
  group: "20px"
  section: "32px"
components:
  button:
    backgroundColor: "#315fe9"
    color: "#ffffff"
    borderRadius: "7px"
---

## Overview

An observability workbench for a Pi operator: see real execution state, build a decision, and inspect why an action was accepted or rejected. Product familiarity and clear state lead. The signature is the execution trace connected to the full option distribution. No invented activity or decorative charts.

## Colors

Cool daylight working surfaces accompany a slate navigation column. Cobalt identifies actions and selection; green, amber, and red denote explicit states with text labels. Dark mode remaps semantic roles to slate surfaces with readable cool text. Color never represents calibrated correctness.

Runtime CSS in frontend/src/style.css owns token values (Model B). DESIGN.md mirrors and explains them. colors.primary → --primary → shared Button/link/focus; canvas/surface/ink/muted/border map directly to the equivalent CSS custom properties. Semantic status colors feed shared Status and probability-chart styles. Typography roles map to --font-display, --font-body, --font-data; radii and spacing map directly to --radius-control, --radius-panel, --space-*.

## Typography

Self-host IBM Plex Sans for headings, Public Sans for body and controls, and IBM Plex Mono only for measurements, identifiers, and code. Main body is 14px/1.55; page titles 28px/1.2; dense measurement labels 12px. No external font requests.

## Layout

Desktop: 224px navigation, compact header, fluid content with a wide operational region and a narrower context region. Content panels use 20px internal spacing and 32px between task groups. Below 900px the navigation collapses to a labeled compact rail; below 640px use a mobile drawer and one content column. Tables own horizontal overflow. Long forms use document scrolling.

## Elevation & Depth

Borders divide operational surfaces. Only overlays receive a soft offset shadow. Avoid nested cards, ornamental backgrounds, glowing status marks, and decorative animation.

## Shapes

Controls use 7px corners, panels 12px, and small status indicators may use rounded capsules. Graph nodes use the control shape and consistent ports. Shared Lucide outline icons use a 1.7px stroke.

## Components

Canonical owners: frontend/src/ui.tsx for Button, Field, Select, Textarea, Status, Dialog, Empty, ErrorPanel, pagination and notifications; frontend/src/forms.tsx for guided schema fields; frontend/src/api.ts for requests and stale-safe resources. Native selects deliberately retain operating-system popup behavior. Every action has keyboard, hover, disabled, loading, success and error behavior. Graph movement and connection creation also have form controls.

## Do's and Don'ts

Show measured quantities and real empty states. Keep simulation and external action authorization explicit. Keep probability concentration separate from correctness. Preserve user input on errors. Use a single owner for feedback and forms. Do not use browser alert/confirm/prompt, fake live data, external assets, unnecessary motion, or color-only states.
