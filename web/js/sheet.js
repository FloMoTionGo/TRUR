// The bottom sheet on phones (docs/mobile.md §6.4): one <dialog>. openSheet moves existing content in, and
// closing puts it back where it was, so there is no duplicate DOM and test ids stay unique.

import { PHONE_QUERY } from "./grid.js";

const sheet = document.getElementById("sheet");
let content = null;  // the element on loan to the sheet
let home = null;     // where it goes back: { parent, next }
let opener = null;

export function openSheet(el, from) {
  if (sheet.open) return;
  content = el;
  opener = from;
  home = { parent: el.parentElement, next: el.nextElementSibling };
  sheet.append(el);
  sheet.showModal();
}

export function closeSheet() {
  if (sheet.open) sheet.close();
}

// Every way of closing ends here: the close button, Esc or the back gesture (cancel), a backdrop tap,
// the layout leaving phone width, and a screen change.
sheet.addEventListener("close", () => {
  if (content) home.parent.insertBefore(content, home.next);
  content = null;
  if (opener) opener.focus(); // iOS doesn't focus a tapped button, so the native return would go to <body>
});
sheet.addEventListener("click", (event) => { if (event.target === sheet) closeSheet(); }); // the backdrop
sheet.querySelector(".sheet-close").addEventListener("click", closeSheet);
matchMedia(PHONE_QUERY).addEventListener("change", (event) => { if (!event.matches) closeSheet(); });
