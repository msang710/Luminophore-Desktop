[**한국어**](README.md) · [English](README.en.md)

<div align = center>

<img src="https://raw.githubusercontent.com/hyprwm/Hyprland/main/assets/header.svg" width="750" height="300" alt="banner">

<br>

[![Badge Workflow]][Workflow]
[![Badge License]][License] 
![Badge Language] 
[![Badge Pull Requests]][Pull Requests] 
[![Badge Issues]][Issues] 
![Badge Hi Mom]<br>

<br>

Hyprland는 외형을 포기하지 않는 100% 독립형 동적 타일링 Wayland 컴포지터입니다.

최신 Wayland 기능, 높은 커스터마이징 자유도, 다양한 시각 효과, 강력한 플러그인,
쉬운 IPC와 다른 컴포지터보다 많은 편의 기능을 제공합니다.
<br>
<br>

---

**[<kbd> <br> 설치 <br> </kbd>][Install]** 
**[<kbd> <br> 빠른 시작 <br> </kbd>][Quick Start]** 
**[<kbd> <br> 설정 <br> </kbd>][Configure]** 
**[<kbd> <br> 기여 <br> </kbd>][Contribute]**

---

<br>

</div>

# 기능

- gradient border, blur, animation, shadow 등 다양한 시각 효과
- 높은 커스터마이징 자유도
- wlroots, libweston, kwin, mutter에 의존하지 않는 100% 독립형 구조
- 자연스러운 애니메이션을 위한 사용자 정의 bezier curve
- 강력한 플러그인 지원
- 내장 플러그인 관리자
- 게임 성능을 위한 tearing 지원
- 확장하기 쉽고 읽기 쉬운 코드베이스
- 빠르고 활발한 개발
- 최신 기능을 적극적으로 제공
- 설정 저장 즉시 reload
- 완전히 동적인 workspace
- 두 가지 내장 layout과 플러그인으로 추가 가능한 layout
- 선택한 앱에 전달되는 global keybind
- tiling / pseudotiling / floating / fullscreen window
- window group(tabbed mode)
- 강력한 window / monitor / layer rule
- socket 기반 IPC
- native IME와 Input Panel 지원
- 그 외 다양한 기능

<br>
<br>

<div align = center>

# 갤러리

<br>

![Preview A]

<br>

![Preview B]

<br>

![Preview C]

<br>
<br>

</div>

# 특별 감사

<br>

**[wlroots]** - *과거 Hyprland의 기반이 되어준 프로젝트*

**[tinywl]** - *구현 방법을 보여준 프로젝트*

**[Sway]** - *구현을 아주 철저하게 하는 방법을 보여준 프로젝트*

**[Vivarium]** - *단순하게 구현하는 방법을 보여준 프로젝트*

**[dwl]** - *hacky한 방식으로 구현하는 방법을 보여준 프로젝트*

**[Wayfire]** - *그래픽 구현의 여러 방법을 보여준 프로젝트*


<!----------------------------------------------------------------------------->

[Configure]: https://wiki.hypr.land/Configuring/
[Stars]: https://starchart.cc/hyprwm/Hyprland
[Hypr]: https://github.com/hyprwm/Hypr

[Pull Requests]: https://github.com/hyprwm/Hyprland/pulls
[Issues]: https://github.com/hyprwm/Hyprland/issues
[Todo]: https://github.com/hyprwm/Hyprland/projects?type=beta

[Contribute]: https://wiki.hypr.land/Contributing-and-Debugging/
[Install]: https://wiki.hypr.land/Getting-Started/Installation/
[Quick Start]: https://wiki.hypr.land/Getting-Started/Master-Tutorial/
[Workflow]: https://github.com/hyprwm/Hyprland/actions/workflows/ci.yaml
[License]: LICENSE


<!----------------------------------{ Thanks }--------------------------------->

[Vivarium]: https://github.com/inclement/vivarium
[WlRoots]: https://gitlab.freedesktop.org/wlroots/wlroots
[Wayfire]: https://github.com/WayfireWM/wayfire
[TinyWl]: https://gitlab.freedesktop.org/wlroots/wlroots/-/blob/master/tinywl/tinywl.c
[Sway]: https://github.com/swaywm/sway
[DWL]: https://codeberg.org/dwl/dwl

<!----------------------------------{ Images }--------------------------------->

[Preview A]: ./assets/prev1.png
[Preview B]: ./assets/prev2.png
[Preview C]: ./assets/prev3.png


<!----------------------------------{ Badges }--------------------------------->

[Badge Workflow]: https://github.com/hyprwm/Hyprland/actions/workflows/ci.yaml/badge.svg

[Badge Issues]: https://img.shields.io/github/issues/hyprwm/Hyprland
[Badge Pull Requests]: https://img.shields.io/github/issues-pr/hyprwm/Hyprland
[Badge Language]: https://img.shields.io/github/languages/top/hyprwm/Hyprland
[Badge License]: https://img.shields.io/github/license/hyprwm/Hyprland
[Badge Lines]: https://img.shields.io/tokei/lines/github/hyprwm/Hyprland
[Badge Hi Mom]: https://img.shields.io/badge/Hi-mom!-ff69b4

### 공간 데스크톱 전용

번호/이름 기반 workspace 탐색, 이전/왕복 history, workspace swipe와 workspace
relocation dispatcher는 제거되었습니다. Luminophore board/view action을 사용하세요.
내부의 모니터별 base desktop과 Spotify service container는 유지되지만 사용자
workspace는 아닙니다. 제거된 기존 command와 selector는 명시적으로 거부됩니다.
