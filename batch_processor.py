"""Archived legacy batch-import entry; execution is permanently blocked."""

from legacy_entry_block import block_legacy_entry


def main() -> None:
    block_legacy_entry("batch_processor.py")


if __name__ == "__main__":
    main()
