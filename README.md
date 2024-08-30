# ssm-env-sync

Keep `.env` files and AWS Systems Manager Parameter Store in sync: diff, pull and push with a dry-run first.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Application settings drift between local `.env` files and Parameter Store, and nobody knows which copy is right.

## Who it is for

Teams that store application configuration in Parameter Store.

## How it will work

1. You point the tool at a parameter path and a file.
2. It compares the two and shows what differs.
3. You pull or push the differences after reviewing them.

## Planned features

Unticked items are not built yet.

- [ ] Diff a `.env` file against a Parameter Store path
- [ ] Pull parameters into a `.env` file
- [ ] Push with a dry-run and confirmation
- [ ] SecureString support with your KMS key
- [ ] Never prints secret values unless asked

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
ssm-env-sync diff --path /myapp/prod --file .env.prod
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
