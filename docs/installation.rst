.. highlight:: shell

============
Installation
============

Prerequisites
-------------

- Python 3.10 or higher
- Internet connection

Source Setup
------------

Clone the repository and install dependencies:

.. code-block:: console

    $ git clone https://github.com/BoPeng/ai-marketplace-monitor.git
    $ cd ai-marketplace-monitor
    $ uv sync

Install a browser for Playwright:

.. code-block:: console

    $ playwright install

For community-contributed instructions, see:

- `Community installation instructions #234 <https://github.com/BoPeng/ai-marketplace-monitor/issues/234>`_

Linux Installation
------------------

.. include:: linux-installation.md
    :parser: myst_parser.sphinx_

Development Installation
------------------------

If you want to contribute to the project:

.. code-block:: console

    $ git clone https://github.com/BoPeng/ai-marketplace-monitor.git
    $ cd ai-marketplace-monitor
    $ uv sync --extra dev
    $ playwright install

This sets up the project for direct source execution with development dependencies using `uv`.
