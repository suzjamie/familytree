# Family tree website generator

Turns a GEDCOM export from Family Tree Maker, plus your image folder, into a
static website for GitHub Pages. No frameworks or build tools: just Python.

## What you get
- A page for every person: life events, family links, photos, notes, sources,
  a 4-generation ancestor chart and a descendant list
- People page (A to Z by surname with a letter strip, live search), places list
- A photo gallery with search and a click-to-enlarge viewer
- Mobile-friendly, dark-mode aware, no external dependencies

## One-time setup
1. Install Python 3 and Pillow: `pip install pillow` (Pillow resizes images; without
   it they are copied at full size).
2. Put these in this folder (or point `site.json` at them):
   - your GEDCOM export, e.g. `tree.ged`
   - your image folder, e.g. `media/` (subfolders are fine)
3. Edit `site.json`: set `gedcom`, `media_dir`, `site_title`, `intro`,
   `domain` (e.g. `family.example.com`) and `home_person` (the ID of the person
   to feature on the home page, such as `@I1@`; optional).

## Build
    python build.py

The site is written to `docs/`. Open `docs/index.html` in a browser to preview.
`build-report.txt` lists any images the tree refers to that weren't found in
your media folder. Images are matched by file name, so keep names unique.

Try it first with the sample: it is already configured in `site.json`.

## Publish on GitHub Pages
1. Publish only the generated `docs/` folder. **Do not put your GEDCOM file or `site.json` in a public
   repo**: the GEDCOM holds details of living people, and `site.json` holds your email address.
   Easiest: make the repo contain just the `docs` folder (the included `.gitignore` also keeps
   `*.ged` and `site.json` out if you use Git on the whole project folder).
2. Repo Settings → Pages → Deploy from a branch → `main` and folder `/docs`.
3. Custom domain: enter your domain on that page (the build also writes `docs/CNAME`
   if `domain` is set) and tick Enforce HTTPS.
4. DNS at your registrar, for an apex domain: four A records pointing to
   185.199.108.153, 185.199.109.153, 185.199.110.153, 185.199.111.153.
   For `www`: a CNAME record pointing to `<your-username>.github.io`.

## Updating
Edit the tree in Family Tree Maker, export GEDCOM again, run `python build.py`,
commit and push. Already-resized images are reused, so rebuilds are fast.

## Choosing which images appear
Only images attached to people (or families) in the tree are used. To trim:
- `max_images_per_person`: cap per person (portrait first), `0` = no cap
- `exclude_images`: list of file names to leave out
- `web_image_px` / `thumb_px`: lower `web_image_px` (e.g. 1200) to shrink the repo
GitHub recommends keeping a Pages site under about 1 GB.

## Living people
`"hide_living": true` shows anyone with no death record born within the last
110 years only as "Living", with no page, photos or details. It is off by default.

## Header, menu and logo
The header, menu bar and footer follow the layout of a purchased template (slate header,
dark menu bar, light footer). In `site.json`:
- `site_title`: the large title in the header (`tagline` is no longer shown; it is only used as the page description for search engines)
- `header_links`: the small stacked links at top right, as `{"label": "...", "url": "..."}`
  (site pages like `people/index.html`, or full web addresses)
- `copyright_text`: the name after the © in the footer (empty = site title)
- `header_image`: path to a wide JPG shown behind the title, full width of the page (2400 x 240 px works well, under about 300 KB; the header is
  made as tall as the picture's shape needs, so the whole picture shows on computer screens, while narrow
  phone screens trim the sides). A light dark tint is laid over it so the white title stays readable. Empty = plain slate.
- `logo`: path to your own logo image; leave empty for the built-in white tree
- `contact_email`: shows a "Contact Me" link in the footer; the address is stored encoded and only becomes a mail link when clicked (no link if left empty)
Fonts (Cormorant Garamond, Jura, Open Sans) load from Google Fonts for visitors; the site
falls back to similar system fonts if they can't load.

## Places page
Places are grouped by country, then by the county, district or state just below the country,
then by address. In the United States the order is state, then county, then address. Place
names are read from the comma-separated place field in your tree (most specific first,
country last). US places that end in a state, such as "Providence, Rhode Island", are filed
under United States even without "USA". A US place with only one part before the state
("Providence, Rhode Island") has no county, so it is listed directly under the state.
Each place has its own expand/collapse arrow and a "Map" link that opens the full place name in Google Maps in a new tab. Place names on person pages (life events and marriages) and in the people list have the same Map link. A place with no address (only a country, state or region) is a row named after that country or region, at the same level as the addresses. Spelling differences
(for example Vienna and Wien) stay separate groups, so fixing the spelling in Family Tree
Maker merges them.

## Tagging faces in group photos
Every build also writes `tagger.html` next to `site.json`. It is for you only (it lists everyone in
your tree), so it is kept out of `docs/` and out of the repo.
1. Open `tagger.html` in your browser (double-click it; build first so the photos exist).
2. Pick a photo on the left. The list starts with group photos (two or more people linked to them).
3. Click a face, then type a name. People already linked to that photo are listed first, marked ★.
   Press Enter to pick the first match. Click a marker to remove it.
4. Click **Download tags.json**, move the file into the `genealogy-site` folder (replace the old one)
   and run the build again. Your work is also kept as a draft in the browser until you download.

On the site, a tagged photo opens its own page: hover (or tap) a face to see the name, which links to
that person's page, or use **Show all names**. Tagged people also get the photo on their own page.
Untagged photos work as before. `tags_file` in `site.json` changes the file name.

## The Grandparents (home page)
`grandparents` in `site.json` lists four people, left to right, for the photo band on the home page.
Use a name as it appears in your tree or a GEDCOM id such as `@I123@`; an empty `""` leaves that card
blank. Each person is a round portrait (their primary photo) with the name above it and years and birthplace
below; the name and photo link to their page. Set the list to `[]` to hide the band.

## Using more than one GEDCOM file (for example paternal and maternal sides)

In `site.json`, give `gedcom` a list instead of a single name:

    "gedcom": ["paternal.ged", "maternal.ged"],

Everyone from both files appears together on one site (People, Places, Photos and the Chart). The trees stay
separate unless you merge them in Family Tree Maker first, so the same person in both files would show twice.
If your pictures are in two folders, `media_dir` can be a list too: `"media_dir": ["paternal-pics", "maternal-pics"]`.
`home_person` should be an id from the first file. A grandparent can still be entered by name.
