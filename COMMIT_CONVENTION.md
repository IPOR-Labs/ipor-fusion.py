# Git Commit Convention

## Commit Message Prefixes

Use the following prefixes for commit messages:

- `[feat]`: New feature or significant enhancement
- `[fix]`: Bug fix
- `[docs]`: Documentation updates
- `[refactor]`: Code refactoring without changing functionality
- `[test]`: Adding or modifying tests
- `[chore]`: Maintenance tasks, build changes, dependency updates
- `[perf]`: Performance improvements
- `[style]`: Code style changes (formatting, linting)
- `[ci]`: Continuous integration changes

## Message Format

```
[prefix] Short description of change

Optional longer description with more details if needed.
```

## Examples

```
[feat] Add user authentication
[fix] Resolve memory leak in config loader
[docs] Update README with installation instructions
[refactor] Simplify configuration management
```

## Guidelines

- Use imperative mood ("Add feature" not "Added feature")
- Capitalize first letter
- No period at the end of the title
- Provide context and motivation for the change in the body if needed