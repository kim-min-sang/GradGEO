#!/bin/bash
# Wait for the httpx retry (step 1) to finish, then start the browser retry (step 2).
cd /home/kms/github/doctor/GradGEO
source /home/kms/anaconda3/etc/profile.d/conda.sh && conda activate gradgeo
while kill -0 7841 2>/dev/null; do sleep 30; done   # step-1 PID (timeout wrapper)
echo "$(date '+%F %T') step 1 finished -> starting step 2 (browser, top 20 hosts, direct)" >> logs/chain_retry.log
nice -n 10 python crawl/crawl_sageo.py --phase browser --top_hosts 20 --no_proxy --num_browsers 4 > logs/crawl_browser.log 2>&1
echo "$(date '+%F %T') step 2 finished (exit $?)" >> logs/chain_retry.log
