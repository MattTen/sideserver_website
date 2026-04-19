# SCInsta builder — image Docker one-shot invoquee par systemd.
#
# Pipeline :
#   1. git clone SoCuul/SCInsta (branche main, FRESH a chaque build)
#   2. Theos compile SCInsta.dylib, FLEXing.dylib, libflex.dylib (SIDELOAD=1)
#   3. cyan injecte les dylibs dans l'IPA Instagram uploadee (+ min iOS 15.0)
#   4. ipapatch patch l'app + ses extensions pour le sideloading
#   5. (optionnel) script de patch.py supplementaire choisi dans l'UI
#   6. depot de l'IPA final dans /srv/store/ipas/ + result JSON dans /etc/ipastore/
#
# L'image est volontairement grosse (Theos + SDK + clang) : c'est un outil de
# build non execute en continu, rebuild uniquement quand la pipeline evolue.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8

# Dependances Theos (officielles, cf. https://theos.dev/docs/linux-instructions).
# - clang / make / perl : compilation des tweaks iOS
# - fakeroot : requis par dpkg-deb invoque par Theos (meme si on ne produit
#   pas de .deb, make package touche dpkg).
# - git : clone SCInsta + installation de Theos + cyan + ldid
# - cmake : build de FLEXing / libflex (CMakeLists dans le repo SCInsta)
# - libxml2 : lief via pip a besoin de libxml2 sur certaines plateformes
# - python3-venv + pip : runtime Python 3.13 via pipx/venv pour cyan
# - curl / ca-certificates : download des SDKs iOS
# - unzip / xz-utils / bzip2 / zip : manipulations des archives (IPA, SDK)
# - zstd : compression utilisee par les archives Theos modernes
# - rsync : copie des dylibs (sync atomique)
# - libplist-utils / libplist-dev : plutil/plistutil + headers pour ldid
# - libssl-dev : openssl headers, requis par ldid pour signer
# Note : le paquet `ldid` n'est pas dispo dans bookworm, on le compile
# depuis ProcursusTeam/ldid dans le RUN suivant.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential clang make perl fakeroot \
        git git-lfs curl ca-certificates unzip xz-utils bzip2 zip zstd rsync \
        cmake pkg-config libplist-utils libplist-dev libssl-dev \
        python3 python3-pip python3-venv python3-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install --system

# ─── ldid (absent de Debian bookworm) ─────────────────────────────────
# Fork moderne ProcursusTeam (utilise par Theos). Build <30s, binaire seul.
RUN git clone --recursive https://github.com/ProcursusTeam/ldid.git /tmp/ldid \
    && make -C /tmp/ldid -j"$(nproc)" \
    && install -m 0755 /tmp/ldid/ldid /usr/local/bin/ldid \
    && rm -rf /tmp/ldid

# ─── Theos ─────────────────────────────────────────────────────────────
# Installe Theos dans /opt/theos (path stable, monte-agnostique).
# THEOS doit etre exporte dans l'env pour que make SIDELOAD=1 le trouve.
ENV THEOS=/opt/theos
RUN git clone --recursive https://github.com/theos/theos.git /opt/theos

# ─── Toolchain iOS (cross-compile Linux → iOS arm64) ───────────────────
# Theos invoque $THEOS/toolchain/linux/iphone/bin/clang pour compiler
# les tweaks. Le repo theos/theos ne contient que la scaffolding ; il
# faut telecharger un toolchain prebuild. On utilise le L1ghtmann/llvm-project
# iOSToolchain (version non-Swift, suffisante pour SCInsta — pas de code
# Swift dans les tweaks). C'est ce que le script officiel `install-theos`
# utilise sur Linux x86_64 sans Swift.
# Le tarball contient directement linux/iphone/bin/ etc., on extrait dans
# $THEOS/toolchain/ pour obtenir la structure attendue.
RUN mkdir -p $THEOS/toolchain \
    && curl -sL https://github.com/L1ghtmann/llvm-project/releases/latest/download/iOSToolchain-x86_64.tar.xz \
       | tar -xJf - -C $THEOS/toolchain/ \
    && test -x $THEOS/toolchain/linux/iphone/bin/clang

# ─── iOS SDK ───────────────────────────────────────────────────────────
# SCInsta/build.sh reference iPhoneOS16.2.sdk en dur ("Your chosen SDK..."
# si absent). Mais theos/sdks master n'heberge que 16.5 pour la serie 16.x
# (pas 16.2) — on pull 16.5 et on symlink 16.2 dessus. Les deux SDKs sont
# ABI-compatibles sur la plage d'iOS 15+ ciblee par SCInsta (sideload iOS
# 15.0+), le header diff entre minor versions est negligeable pour les
# tweaks Instagram.
#
# Le repo theos/sdks stocke les SDKs comme des REPERTOIRES tracke par Git
# LFS — on clone avec GIT_LFS_SKIP_SMUDGE=1 pour eviter de pull les ~8 Go
# de tous les SDKs, puis `git lfs pull --include` ne telecharge que les
# binaires du SDK cible.
RUN mkdir -p /opt/theos/sdks \
    && GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --branch master \
        https://github.com/theos/sdks.git /tmp/sdks \
    && git -C /tmp/sdks lfs pull --include="iPhoneOS16.5.sdk/**" \
    && mv /tmp/sdks/iPhoneOS16.5.sdk /opt/theos/sdks/ \
    && ln -s iPhoneOS16.5.sdk /opt/theos/sdks/iPhoneOS16.2.sdk \
    && rm -rf /tmp/sdks

# ─── Stub libclang_rt.ios.a (fix __isOSVersionAtLeast) ─────────────────
# Le toolchain L1ghtmann ne packe pas compiler-rt pour iOS, donc les
# @available(...) dans FLEX/FLEXing produisent des references undefined
# a __isOSVersionAtLeast au link. On fournit un stub qui delegue a
# _availability_version_check (presente dans libSystem iOS 12+), place
# dans le resource-dir de clang pour etre auto-lie par les targets ios.
#
# SCInsta builde en fat arm64+arm64e (cyan CMAKE_OSX_ARCHITECTURES). Le
# linker Apple ignore les objets dans l'archive qui ne matchent pas la
# cible — on compile le stub pour les deux arches et on les bundle dans
# la meme archive (noms distincts pour eviter les collisions).
RUN set -eu \
    && mkdir -p /tmp/rtstub \
    && printf '%s\n' \
        '#include <stdint.h>' \
        'typedef struct { uint32_t major, minor, patch; } _DarwinAvailVersion;' \
        'extern uint32_t _availability_version_check(uint32_t count, _DarwinAvailVersion versions[]);' \
        'int32_t __isOSVersionAtLeast(int32_t major, int32_t minor, int32_t subminor) {' \
        '    _DarwinAvailVersion v = { (uint32_t)major, (uint32_t)minor, (uint32_t)subminor };' \
        '    return _availability_version_check(1, &v);' \
        '}' \
        > /tmp/rtstub/stub.c \
    && RSRC="$($THEOS/toolchain/linux/iphone/bin/clang -print-resource-dir)" \
    && mkdir -p "$RSRC/lib/darwin" \
    && $THEOS/toolchain/linux/iphone/bin/clang \
        -target arm64-apple-ios15.0 \
        -isysroot $THEOS/sdks/iPhoneOS16.5.sdk \
        -c /tmp/rtstub/stub.c -o /tmp/rtstub/stub_arm64.o \
    && $THEOS/toolchain/linux/iphone/bin/clang \
        -target arm64e-apple-ios15.0 \
        -isysroot $THEOS/sdks/iPhoneOS16.5.sdk \
        -c /tmp/rtstub/stub.c -o /tmp/rtstub/stub_arm64e.o \
    && $THEOS/toolchain/linux/iphone/bin/llvm-ar rcs \
        "$RSRC/lib/darwin/libclang_rt.ios.a" \
        /tmp/rtstub/stub_arm64.o /tmp/rtstub/stub_arm64e.o \
    && $THEOS/toolchain/linux/iphone/bin/ranlib \
        "$RSRC/lib/darwin/libclang_rt.ios.a" \
    && rm -rf /tmp/rtstub

# ─── cyan (pyzule-rw) + ipapatch + lief ───────────────────────────────
# cyan s'installe depuis git (absent de PyPI). lief est utilise par les
# scripts patch/*.py, ipapatch depuis son dernier tarball disponible.
# --break-system-packages : Debian 12 a adopte PEP 668, pip refuse d'ecrire
# dans /usr/lib/python3 sans override. Comme le conteneur est ephemere et
# qu'on ne veut pas d'un venv, on passe outre.
RUN pip install --no-cache-dir --break-system-packages \
        "cyan @ git+https://github.com/asdfzxcvbn/pyzule-rw" \
        "lief>=0.16" \
        requests

# ipapatch : CLI Go qui patch l'IPA + ses extensions pour le sideload
# (appele en fin de SCInsta/build.sh). Repo canonique =
# asdfzxcvbn/ipapatch (meme auteur que cyan/pyzule-rw). v2+ est un
# binaire Go natif portable, on telecharge le release linux-amd64 plutot
# que d'installer une toolchain Go. SCInsta/build.sh l'invoque
# obligatoirement — pas de fallback.
#
# Version pinnee : tag le plus recent au moment du build initial. Pour
# mettre a jour, bump le tag ici et rebuild l'image.
ARG IPAPATCH_VERSION=v2.1.3
RUN curl -fsSL \
        "https://github.com/asdfzxcvbn/ipapatch/releases/download/${IPAPATCH_VERSION}/ipapatch.linux-amd64" \
        -o /usr/local/bin/ipapatch \
    && chmod +x /usr/local/bin/ipapatch \
    && ipapatch --version

WORKDIR /opt/builder

# PATH inclut $THEOS/bin pour accès a nic.pl, logos.pl et consorts invoques
# par les Makefiles SCInsta.
ENV PATH=$THEOS/bin:/usr/local/bin:$PATH

COPY build.py /opt/builder/build.py

ENTRYPOINT ["python3", "-u", "/opt/builder/build.py"]
