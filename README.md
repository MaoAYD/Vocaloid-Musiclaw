# 🎵 MusicLaw

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/yourusername/MusicLaw)](https://github.com/yourusername/MusicLaw/releases)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

> 本地优先的专辑信息抓取与标签整理工具，面向合成歌声软件同人专辑  
> A local-first metadata collection and tagging tool for synthetic singing software doujin albums.

[**English**](https://github.com/MaoAYD/Vocaloid-Musiclaw/blob/main/README.en.md) | [**中文**](https://github.com/MaoAYD/Vocaloid-Musiclaw/blob/main/README.zh-CN.md)

---

## 📖 简介 | Introduction

`MusicLaw` 是一个本地优先的专辑信息抓取与标签整理工具，专门面向 VOCALOID、UTAU、Synthesizer V、CeVIO，以及其他合成音声软件相关的同人专辑。它会扫描本地专辑文件夹，搜索 VocaDB、VCPedia、dizzylab 等来源，结合站点规则解析与可选的 OpenAI 兼容 LLM 流程，在人工审核后将元数据写回音频文件。

`MusicLaw` is a local-first metadata collection and tagging tool designed for VOCALOID, UTAU, Synthesizer V, CeVIO, VOICEROID‑derived singing projects, and other synthetic singing software doujin albums. It scans album folders, searches source sites such as VocaDB, VCPedia, and dizzylab, combines site‑specific parsing with an optional OpenAI‑compatible LLM workflow, and writes reviewed metadata back to your local files.

---

## ✨ 功能特点 | Features

- **本地优先**：所有操作在本地执行，保护隐私  
  **Local‑first**: all operations run locally to protect your privacy.
- **多源抓取**：支持 VocaDB、VCPedia、dizzylab 等站点  
  **Multi‑source fetching**: supports VocaDB, VCPedia, dizzylab, and more.
- **智能解析**：站点专用解析 + 可选 LLM 增强（兼容 OpenAI API）  
  **Smart parsing**: site‑specific rules + optional LLM enhancement (OpenAI‑compatible API).
- **人工审核**：抓取信息后由用户确认，确保准确性  
  **Human review**: metadata is confirmed by the user before writing.
- **写回本地**：将审核后的元数据写入音频文件标签  
  **Write‑back**: writes reviewed metadata into audio file tags.

---

## 🚀 快速开始 | Quick Start

### 前提条件 | Prerequisites

- Python 3.10+
- (可选) OpenAI API 密钥（用于 LLM 增强）  (推荐使用 Strong Recommendation)
  (Optional) OpenAI API key for LLM enhancement.

### 安装 | Installation

```bash
git clone https://github.com/MaoAYD/Vocaloid-Musiclaw.git
cd Vocaloid-Musiclaw
pip install -r requirements.txt
pip install -e .
scrapling install
