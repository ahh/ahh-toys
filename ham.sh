#!/bin/bash
function echoerr { echo "$@" 1>&2; }
function ldebug {
#    echoerr $@
    true
}

function ham
{
    set -e
    read VERTEX EDGES
    #REST=$(mktemp -t ham)
    #cat > ${REST}
    REST=$(cat)
    ldebug V=${VERTEX} E='"'${EDGES}'"' $@ $(echo -e "${REST}" | tr '\n' ,)

    if [[ -n "${REST}" ]]; then
	for i in ${EDGES}
	do
	    if [[ "${VERTEX}" != "$i" ]]; then
		echo -e "${REST}" | sed -e "s/^$i /${VERTEX} /" | sed -e "s/ $i//" | prep $@:"${VERTEX}->$i" | sed -e "s/${VERTEX}/${VERTEX} $i/" #| head -n 1
	    fi
	done
    else
	for i in ${EDGES}
	do
	    if [[ "${VERTEX}" = "$i" ]]; then
#		echoerr V=${VERTEX} E='"'${EDGES}'"' $@ $(echo -e "${REST}" | tr '\n' ,)
		echo $i
	    else
#		echoerr WTF? V=${VERTEX} E='"'${EDGES}'"'
		exit 1
	    fi
	done
    fi
}
function transpose {
    awk '{ trans[$1] = trans[$1]; for (i=2; i<=NF; i++) trans[$i] = trans[$i]" "$1;} END {for (team in trans) print team""trans[team]; }'
}

function deg
{
    awk 'NR == 1 { print NF }' 
}

function gsort
{
    awk '{print NF,$0}' | sort -k 1 -n | sed -e 's/^[0-9]* //'
}

function revwords
{
    awk '{for (i=NF; i > 1; i--) printf("%s ",$i); printf("%s\n", $1)}'
}

function prep
{
    GRAPH=$(gsort)
    TGRAPH=$(echo -e "${GRAPH}" | transpose | gsort)
    (if [[ $(echo -e "${GRAPH}" | deg) -le $(echo -e "${TGRAPH}"| deg) ]]; then
	echo -e "${GRAPH}"| ham $@ 
    else
	echo -e "${TGRAPH}" | ham $@ | revwords
    fi)
}


prep "" #| head -n 1
exit
