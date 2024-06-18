#!/usr/bin/zsh

REPOS=(datoso datoso_plugin_internetarchive datoso_seed_base datoso_seed_fbneo datoso_seed_md_enhanced datoso_seed_nointro datoso_seed_pleasuredome datoso_seed_private datoso_seed_redump datoso_seed_sfc_enhancedcolors datoso_seed_sfc_msu1 datoso_seed_sfc_speedhacks datoso_seed_tdc datoso_seed_translatedenglish datoso_seed_vpinmame datoso_seed_whdload)

cd ..
for repo in $REPOS; do
    echo "Change $repo branch to master"
    cd $repo
    git checkout master
    git pull origin master --rebase
    cd ..
done