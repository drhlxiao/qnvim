# qnvim

A minimal gvim-style Qt GUI for Neovim. It renders Neovim's grid through `ext_linegrid`, builds the menu bar from Neovim's `menu.vim`, and provides a toolbar.

## Requirements

- Neovim 0.9 or newer on `PATH`
- Python 3
- Python packages: `pynvim`, `PySide6`
- Optional: the JetBrainsMono Nerd Font (the editor falls back to another font if it is missing)

```bash
pip install pynvim PySide6
```

## Usage

```bash
python qnvim.py [files...]
```

## Keyboard

Keys are sent to Neovim as they are, so Vim modes and mappings work normally.

| Key | Action |
| --- | --- |
| Ctrl+C in the launching terminal | Quit, with the unsaved-changes prompt |
| Close window button | Quit, with the unsaved-changes prompt |
| Any other key | Sent to Neovim |

Ctrl+C inside the window is sent to Neovim, not used for quitting.

## Quitting and unsaved changes

When you quit, the app checks for modified buffers. If there are any, a dialog lists them with three choices:

- **Save All** saves named buffers with `:wa`. Unnamed buffers cannot be saved this way, so they stay in the list and the dialog appears again.
- **Discard** quits without saving.
- **Cancel** keeps the app open.

## Toolbar

| Button | Action |
| --- | --- |
| New | `:enew` |
| Open | File dialog, then `:edit` |
| Save | `:write` |
| Undo / Redo | `:undo` / `:redo` |
| Cut / Copy / Paste | Edit menu items (`emenu Edit.Cut` and so on) |
| Find | Enters `/` search |

## Menus

The menu bar is generated from Neovim's `menu.vim` when the app starts. Each item runs its `emenu` command, so the menus match whatever Neovim provides.

## Files

- `qnvim.py`: the whole application
