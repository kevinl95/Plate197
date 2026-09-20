"""The page and the server have to agree on what a bird is called.

Both sides reduce a name to a matching token — the page to decide
whether a bird has a picture, the server to find the file. If those two
reductions ever drift apart the failure is silent: the plate exists, the
page believes it doesn't, and the bird shows a silhouette forever with
nothing in any log about it. So they are compared directly.

Skipped where node isn't installed; the rule is still covered on the
Python side by test_plates.py.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from earshot import plates

PAGE = Path(__file__).resolve().parent.parent / "index-7inch.html"

NAMES = [
    "House Finch", "house-finch.png", "House Finch.PNG", "house_finch",
    "Black-capped Chickadee", "black_capped_chickadee.png",
    "Wilson's Warbler", "Wilson’s Warbler", "Müller's Barbet",
    "Cedar Waxwing.png", "cedar waxwing.webp", "spottedtowhee.PNG",
    "Red-breasted Nuthatch", "Black-billed Magpie", "Lesser Goldfinch",
    "crimson-necked-bull-finch.png",
]

HARNESS = """
const fs = require('fs');
const src = fs.readFileSync(%s, 'utf8').match(/<script>([\\s\\S]*)<\\/script>/)[1];
const el = () => ({ textContent:'', innerHTML:'', style:{setProperty(){}},
  classList:{add(){},remove(){},toggle(){}}, addEventListener(){},
  querySelector:()=>null, appendChild(){}, children:[] });
global.document = { getElementById: el, createElement: el };
global.setInterval = () => {}; global.setTimeout = () => {};
global.fetch = async () => { throw new Error('offline'); };
const api = new Function('with(this){' + src + '; return {plateKey};}').call({...global});
console.log(JSON.stringify(%s.map(n => api.plateKey(n))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_page_and_the_server_reduce_names_identically(tmp_path):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS % (json.dumps(str(PAGE)), json.dumps(NAMES)))
    out = subprocess.run(["node", str(script)], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    from_page = json.loads(out.stdout)
    from_server = [plates.key(n) for n in NAMES]
    mismatched = [(n, a, b) for n, a, b in zip(NAMES, from_server, from_page)
                  if a != b]
    assert not mismatched, f"page and server disagree: {mismatched}"


def test_the_page_still_previews_on_its_own():
    # Opened as a file with no server, CONFIG.API is null, and the page
    # must fall back to mock data and drawn silhouettes rather than
    # trying to fetch plates from nowhere.
    source = PAGE.read_text()
    assert re.search(r"API\s*:\s*null", source), "mock preview would be lost"
    assert "if(!CONFIG.API) return;" in source, "loadPlates must no-op offline"


def test_every_species_entry_has_what_drawing_needs():
    source = PAGE.read_text()
    block = source[source.index("const SPECIES"):source.index("ARTWORK")]
    entries = re.findall(r"'([^']+)':\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}", block)
    assert len(entries) >= 15, f"only found {len(entries)} species"
    for name, body in entries:
        assert "mass_g" in body, f"{name} has no mass_g, so it cannot be sized"
        assert "sci" in body, f"{name} has no scientific name"


def test_the_committed_page_carries_no_real_location():
    """The page in the repository must stay a placeholder.

    Its footer is substituted from config at serve time, so there is
    never a reason to type a real town into this file — and this
    repository is public.
    """
    from earshot.config import Config
    source = PAGE.read_text()
    in_page = re.search(r"LOCATION\s*:\s*'([^']*)'", source).group(1)
    assert in_page == Config().location, (
        f"the page says {in_page!r} but the config default is "
        f"{Config().location!r}; set the real one in earshot.toml instead")
