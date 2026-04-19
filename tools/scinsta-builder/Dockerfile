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

# ipapatch : installe depuis le repo officiel (CLI python standalone).
# Nom canonique du repo : SideStore/ipapatch (anciennement JJTech/ipapatch).
# Fallback sur une installation no-op si indisponible — le build reste
# fonctionnel avec cyan seul pour la majorite des cas d'usage.
RUN pip install --no-cache-dir --break-system-packages \
        "ipapatch @ git+https://github.com/JJTech0130/ipapatch" \
    || echo "[WARN] ipapatch non installe — build continuera sans (cyan seul suffit souvent)."

WORKDIR /opt/builder

# PATH inclut $THEOS/bin pour accès a nic.pl, logos.pl et consorts invoques
# par les Makefiles SCInsta.
ENV PATH=$THEOS/bin:/usr/local/bin:$PATH

COPY build.py /opt/builder/build.py

ENTRYPOINT ["python3", "-u", "/opt/builder/build.py"]
