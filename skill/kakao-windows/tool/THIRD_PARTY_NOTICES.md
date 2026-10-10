# Third-party notices

This project adapts selected read-only key recovery, SQLCipher v4 page decoding, and KakaoTalk text decoding logic from [is-theo/kakao-cli-win](https://github.com/is-theo/kakao-cli-win), commit 411d0dd1b472ea8a0cc8bf399e3351c0651748b6, copyright (c) 2026 theo, MIT license. The full MIT notice is included in LICENSE and applies to redistributed portions.

The standalone refactor adds x64 layout handling, authenticated snapshots with committed WAL, user-protected key storage, bounded exports, per-user setup, and scheduling. It does not include original user data or an upstream database cache.

Runtime dependency: [cryptography](https://cryptography.io/) (Apache-2.0 OR BSD-3-Clause), with its wheel dependencies cffi and pycparser under their respective licenses. Installed wheels include the applicable license files. Python is installed separately by the recipient under the Python Software Foundation License. No Python interpreter or third-party binary is bundled in the source ZIP.

Format references: [SQLCipher source](https://github.com/sqlcipher/sqlcipher/blob/v4.6.1/src/sqlcipher.c), [SQLite file/WAL format](https://sqlite.org/fileformat2.html), [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata), [Claude Code skills](https://code.claude.com/docs/en/skills).

This is an unofficial community tool, not a Kakao or Anthropic product.
