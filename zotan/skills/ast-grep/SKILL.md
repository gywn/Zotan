---
name: ast-grep
description: Use ast-grep for massive structural code search and replacement. Use when user wants to refactor, rename, add patterns, or make bulk changes across a codebase - especially for 3+ files with complex AST-based transformations. Supports pattern matching with meta variables ($VAR, $$$VAR), YAML rules, JSON output, and 20+ languages including JavaScript, Python, Rust, Go.
---

# ast-grep: Structural Code Search and Replace

ast-grep (`sg` or `ast-grep`) uses tree-sitter AST parsing for semantic pattern matching. Use for bulk code refactoring across multiple files.

## When to Use

**USE when:** Renaming functions/classes, changing call patterns, converting code (var→const, lambda→def), refactoring APIs, 3+ files need similar changes.

**AVOID when:** 1-2 files, unique per-file changes, non-code files (JSON/YAML/Markdown), simple text replacement.

## Quick Start

### 1. Search (Dry Run)
```bash
sg run -p 'PATTERN' -l python --json .
sg scan --rule rule.yml --json .
```

### 2. Preview Rewrite
```bash
sg run -p 'PATTERN' -r 'REPLACEMENT' -l python .
```

### 3. Apply Changes
```bash
sg run -p 'PATTERN' -r 'REPLACEMENT' -U .
```

## Pattern Syntax

### Meta Variables

| Pattern | Matches |
|---------|---------|
| `$VAR` | Any single AST node |
| `$$$VAR` | Zero or more AST nodes |
| `$_VAR` | Non-capturing (faster) |
| `$$VAR` | Unnamed tree-sitter nodes |

**Naming:** `$A`, `$FUNC`, `$ARG1`, `$$$ARGS` (valid) vs `$lowercase`, `$123` (invalid)

### Capturing

Same-name variables capture AND reuse:
```bash
# $A == $A matches: a==a, x==x
# Does NOT match: a==b
sg run -p '$A == $A' -l python --json .
```

Rewrite using captured: `sg run -p '$X = $Y' -r '$Y = $X'` swaps sides.

### JavaScript/TypeScript
```bash
# Function calls
sg run -p 'console.log($MSG)' -l javascript --json .
sg run -p '$OBJ.$METHOD($$$ARGS)' -l javascript --json .

# Declarations
sg run -p 'var $X = $Y' -l javascript --json .
sg run -p 'const $X = $Y' -l javascript --json .
sg run -p 'const $NAME = ($ARGS) => $BODY' -l javascript --json .
```

### Python
```bash
# Functions
sg run -p 'def $FUNC($$$ARGS):' -l python --json .
sg run -p 'def $FUNC($ARG: $TYPE) -> $RET:' -l python --json .

# Lambda
sg run -p 'lambda $ARGS: $BODY' -l python --json .

# Imports
sg run -p 'from $MODULE import $$$NAMES' -l python --json .

# Remove module docstrings
sg run -p '"""$DOCSTRING"""' -l python -r '' -U .

# Remove class docstrings
sg run -p 'class $NAME:
  """$$$DOCSTRING"""' -l python -r 'class $NAME:' -U .
```

### Rust
```bash
sg run -p 'fn $FUNC($$$ARGS) -> $RET {$BODY}' -l rust --json .
sg run -p 'impl $NAME {$BODY}' -l rust --json .
sg run -p '$X.unwrap()' -l rust --json .
```

### Go
```bash
sg run -p 'func $NAME($$$PARAMS) $RETTYPE {$BODY}' -l go --json .
sg run -p '$X, $ERR := $FUNC()' -l go --json .
```

## Inline Rules

For quick testing without creating YAML files, use `--inline-rules`:

```bash
sg scan --inline-rules 'id: rule-name
language: python
rule:
  pattern: |
    PATTERN_CODE
fix: |
  REPLACEMENT_CODE' --json .
```

Example - Remove Python docstrings:
```bash
sg run --inline-rules 'id: remove-docstring
language: python
rule:
  pattern: """$DOCSTRING"""
fix: ""' -U .
```

## YAML Rules

For complex rules, create `rule.yml`:

```yaml
id: my-rule
language: python
rule:
  pattern: |
    def $FUNC($ARG):
      $$$BODY
fix: |
  def $FUNC($ARG):
    $$$BODY
    print("Called:", "$FUNC")
```

Run: `sg scan -r rule.yml . -U`

### Multiple Rules
```yaml
---
id: rule-1
rule:
  pattern: foo($X)
fix: bar($X)
---
id: rule-2
rule:
  pattern: old_func($X)
fix: new_func($X)
```

## Common CLI Options

| Option | Description |
|--------|-------------|
| `-p, --pattern` | AST pattern (for `sg run`) |
| `-r, --rewrite` | Replacement string |
| `-l, --lang` | Language (python, javascript, etc.) |
| `-U, --update-all` | Apply all changes without confirmation |
| `--json` | JSON output (pretty, stream, compact) |
| `--globs` | File filter (e.g., "*.py") |
| `-j, --threads` | Number of threads |
| `--stdin` | Read pattern from stdin |
| `-i, --interactive` | Interactive edit session |
| `--no-ignore` | Ignore .gitignore files |
| `--inline-rules` | Define rules inline in CLI |

```bash
# Rename function everywhere
sg run -p 'oldFunc($$$ARGS)' -r 'newFunc($$$ARGS)' -U .

# Convert var to const
sg run -p 'var $X = $Y' -r 'const $X = $Y' -U .

# Python print to logging
sg run -p 'print($MSG)' -r 'logger.info($MSG)' -U .

# Lambda to def
sg run -p '$F = lambda $A: $B' -r 'def $F($A):\n  return $B' -U .

# Remove all docstrings in Python files
sg run -p '"""$DOCSTRING"""' -l python -r '' -U .

# Get list of files with matches (useful for analysis)
sg run -p 'PATTERN' -l python --json . | python3 -c "import json, sys; d=json.load(sys.stdin); print('\n'.join(sorted(set(x['file'] for x in d))))"
```

## Important Notes

1. **ALWAYS dry run first** - use `--json` to preview
2. **Backup with git** before mass changes
3. **Indentation preserved** in rewrite
4. **One rule at a time** for complex refactoring
5. **Command naming:** Use `sg` (shorthand) or `ast-grep` (full)
6. **Pattern limitations:** Multi-line patterns (e.g., class with docstring) may match more than expected - test carefully before applying

## Troubleshooting

- **No matches:** Check pattern is valid code, verify language
- **Too many matches:** Make pattern more specific, use `--globs`
- **Syntax errors:** Ensure replacement is valid code
- **`-p` not recognized:** Use `sg run` not `sg scan` for patterns

## More Details

See `references/` directory for:
- Complete YAML rule schema
- More examples by language
- Advanced features (expandStart, expandEnd, transformations)