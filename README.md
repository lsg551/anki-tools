# anki-tools

`anki-tools` provides tools to commit Anki deck packages (binary *.apkg* files)
as diffable text-based files (e.g., JSON) to version control systems. This is
especially useful if you plan to distribute your Anki decks via GitHub or
other VCS environments while still being able to collaborate.

While editing deck pages (import *.apkg*, re-export and commit) is generally
possible, changes are binary and can not be meaningfully diffed. `anki-tools`
dumps a deck package to canonical text so you can review and merge deck changes
in git, then builds a package back from that text.

It uses the official upstream [`anki`](https://pypi.org/project/anki/) library
and provides a tiny CLI around it.

> [!note]
> This is a very early pre v1.x version. Expect bugs and edge cases. Read
> release notes carefully for breaking changes.

## Example

Suppose you crafted an Anki deck in your desktop GUI and want to share it. The
deck can be exported from the GUI as an *.apkg* file by clicking on the deck in
the list, then clicking the settings icon and *Export*.

```bash
anki-tools dump deck.apkg -o deck/    # .apkg → text, commit it
anki-tools diff deck/ new.apkg        # compare two packages and/or dumps
anki-tools build deck/ -o deck.apkg   # text → .apkg, for release
```

The dump is a directory of JSON (Lines) files (or self-contained in a zip
archive) plus a `media/` directory. It is stable and diffable, so a deck review
reads like a code review.

> [!IMPORTANT]
> The text is not meant as an editing surface though!
> Change cards in Anki and dump again. Do NOT edit the JSON directly.

These are the basic three commands `dump`, `build` and `diff`. The first two are
a pair of idempotent functions used to create a canonical dump and reverse it
$\text{dump}(\text{.apkg})\rightarrow\text{text}$ and
$\text{build}(\text{text})\rightarrow\text{.apkg}$. The `diff` command is only
for user convenience and other diff commands work as well. For GitHub,
`git diff` is normative anyway.

### Git Workflow

The collaboration workflow can be arbitrarily automated with actions/workflows.

Seeding the git repository with a dump from the first *.apkg* version, a
workflow could automatically build a new *.apkg* file from the dump stored in
git and attach it as an release asset so others can download and import it in
their Anki instance.

Collaborators can then make changes in Anki and either contribute a PR with
changes to the dump or their own exported *.apkg*. An automatically triggered
workflow could take the file, create a dump and a human-readable diff along a
PR for merging.

Of course, there are multiple solutions for this.

## Installation

Install the latest release for Linux or macOS:

```bash
curl -fsSL https://github.com/lsg551/anki-tools/releases/latest/download/install.sh | sh
```

The installer uses `uv` if available, then `pipx`, then `python3 -m pip`.
It needs a 3.13+ Python interpreter; `uv` can download one.

Or download the wheel from the release assets and install it:

```bash
pip install anki_tools-*.whl
```

<details><summary>Asset attestation</summary>
<p>
Release assets carry a build-provenance attestation; verify it with:

```bash
gh attestation verify anki_tools-*.whl --repo lsg551/anki-tools
```

</p>
</details>

<details><summary>Build from source</summary>
<p>
To build from source, you must have
<a href="https://github.com/astral-sh/uv"><code>uv</code></a>
installed. Then:

```bash
git clone https://github.com/lsg551/anki-tools.git
uv sync
```

</p>
</details>

## Compatibility

*.apkg* packages carry a format version in their `meta` member, which maps to a
database schema version. `anki-tools` reads any package Anki's importer accepts
and always writes the latest format.

| anki-tools | `anki` lib | dump schema | writes | reads | consumer Anki |
| --- | --- | --- | --- | --- | --- |
| 0.1.x | 26.9.3 | v1 | `latest` (`collection.anki21b`, schema v18) | any package Anki's importer accepts | schema v18 (current Anki) |

Packages using a format newer than the pinned library supports are rejected with
an error.

## Documentation

Documentation comes in form of a single man page:

```bash
man anki-tools
```

The installer should put the man page on the `MANPATH`. If you installed the
wheel manually, run `anki-tools --install-man-pages` (flag is hidden, but it's
there). Re-run the installer after an upgrade to refresh the page.

(`docs/` currently does NOT contain any user documentation,
only for development.)

## Limitations

There are limitations and caveats associated by using deck packages created by
anki-tools. Please see the man page for `anki-tools(1)`
(or [this doc](./man/anki-tools.include)).

## License

MIT. See the [LICENSE](./LICENSE) file for details.
