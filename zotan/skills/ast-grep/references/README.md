# ast-grep Reference Guide

Complete reference material for advanced ast-grep usage.

## CLI Commands

| Command | Description |
|---------|-------------|
| `sg run` / `sg` | One-time search or rewrite (default) |
| `sg scan` | Scan using YAML configuration rules |
| `sg test` | Test ast-grep rules |
| `sg new` | Create new project or items (project, rule, test, util) |
| `sg lsp` | Start language server |
| `sg completions` | Generate shell completions (bash, zsh, fish, powershell, elvish) |

## JSON Output Format

```bash
sg scan --json -p 'PATTERN' .
```

Styles: `pretty` (formatted), `stream` (one JSON per line), `compact` (minimized)

Output fields:
- `file`: File path
- `range`: Start/end line and column
- `content`: Matched code snippet
- `meta`: Capture groups

## Complete YAML Rule Schema

```yaml
id: rule-id
language: javascript
message: "Description of the issue"
severity: warning  # error, warning, info, hint, off

rule:
  # Pattern-based (simple)
  pattern: code($VAR)
  
  # Or kind-based (advanced)
  kind: call_expression
  has:
    field: function
    kind: identifier
    regex: ^foo$
  not:
    kind: optional_expression
  
  # Or relational
  inside:
    kind: function_declaration
  
  # Or anyOf/allOf
  anyOf:
    - kind: identifier
    - kind: member_expression

fix:
  # Simple string
  template: "replacement($VAR)"
  
  # Or with expansion
  template: "replacement"
  expandStart: { kind: comment }
  expandEnd: { regex: ',' }
  
  # Or with transformation
  rewrite:
    to: "NEW_$VAR"
    from: "$VAR"
```

```yaml
id: add-try-catch
language: javascript
rule:
  pattern: $FUNC($$$ARGS)
fix: |
  try {
    $FUNC($$$ARGS)
  } catch (e) {
    console.error(e)
  }
```

### Conditional Rewrite Based on Context

```yaml
id: wrap-in-await
language: javascript
rule:
  pattern: $X.then($FN)
fix: await $X.then($FN)
```

## Kind Selectors by Language

### JavaScript/TypeScript
- `call_expression` - Function calls
- `identifier` - Variable names
- `arrow_function` - Arrow functions
- `function_declaration` - Function declarations
- `class_declaration` - Class declarations
- `pair` - Object property key-value

### Python
- `function_definition` - Function defs
- `class_definition` - Class defs
- `call` - Function calls
- `lambda` - Lambda expressions
- `import_statement` - Imports

### Rust
- `function_item` - Function definitions
- `impl_item` - Impl blocks
- `struct_item` - Struct definitions
- `call_expression` - Function calls

### Go
- `function_declaration` - Function defs
- `call_expression` - Function calls
- `var_spec` - Variable declarations

## Transformations

### String Replace

```yaml
fix:
  rewrite:
    to: "NEW_$VAR"
    from: "$VAR"
    replace: "old"  # Replace in captured value
```

### Case Conversion

```yaml
fix:
  rewrite:
    to: "$VAR"
    case: capitalize  # capitalize, upper, lower
```

## Performance Tips

1. Use `$_VAR` instead of `$VAR` when you don't need to capture
2. Use `--globs` to limit file scope
3. Use `--threads` (`-j`) to control parallelism
4. Use `--no-ignore` sparingly (affects performance)

## Common Issues

### Pattern Not Matching

1. Check pattern is valid, parseable code
2. Verify language with `-l`
3. Try simpler pattern first
4. Use `--debug-query` to see parsed AST

### Rewrite Not Applying

1. Check replacement is valid code
2. Verify meta variable names match
3. Try dry run without `-U`

### Too Many Matches

1. Make pattern more specific
2. Use `kind` selector
3. Use `has`/`not` constraints
4. Limit with `--globs`

## Installation Methods

```bash
# pip (recommended for this skill)
pip install ast-grep-cli

# Homebrew (macOS/Linux)
brew install ast-grep

# Cargo (Rust)
cargo install ast-grep --locked

# npm
npm i @ast-grep/cli -g

# MacPorts
sudo port install ast-grep
```

## Links

- Official: https://ast-grep.github.io/
- GitHub: https://github.com/ast-grep/ast-grep
- Playground: https://ast-grep.github.io/playground.html
- CLI Ref: https://ast-grep.github.io/reference/cli.html
