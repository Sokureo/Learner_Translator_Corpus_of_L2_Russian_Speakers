#  -- coding: utf8 --
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.shortcuts import render_to_response, redirect, render
#from models import Doc, Sentence, Error, Analysis, Token
from annotator.models import Document, Sentence, Annotation, Token, Morphology
from django.views.generic.base import View
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.http import HttpResponse, HttpResponseServerError, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseForbidden
from django.views.generic import View
from django.views.generic.base import TemplateView
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.template import *
from search import *
from collections import Counter
from translator_corpus.settings import PROD
from itertools import chain
from db_utils import Database
import re
import HTMLParser
import xlsxwriter
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO
h = HTMLParser.HTMLParser()
rePage = re.compile(u'&page=\\d+', flags=re.U)
reSpan = re.compile(u'<span class="token"( data-toggle="tooltip")? title=".*?">(.*?)</span>', flags=re.U)
import json


def download_file(doc_id, doc_type):
    db = Database()
    if doc_type == 'ann':
        req = "SELECT `username`, `data`, `tag`, `start`, `end` FROM `annotator_annotation` LEFT JOIN `auth_user` ON annotator_annotation.owner_id=auth_user.id WHERE `document_id` in (SELECT id FROM `annotator_sentence` WHERE `doc_id_id`=%s)" %doc_id
        text = u'Разметчик\tОшибка\tИсправление\tТэг\tНачало ошибки (номер слова от начала предложения)\tКонец ошибки (номер слова от начала предложения)\r\n'
        rows = db.execute(req)
        for row in rows:
            data = json.loads(row[1])
            text += '\t'.join([str(row[0]), data['quote'], data['corrs'], row[2], str(row[3]), str(row[4])]) + '\r\n'
        response = HttpResponse(text, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="annotation_text_%s.csv"' %doc_id
        return response
    elif doc_type == u'text':
        req = "SELECT text FROM `annotator_sentence` WHERE `doc_id_id`=%s" %doc_id
        text = u' '.join(h.unescape(i[0]) for i in db.execute(req))
        response = HttpResponse(text, content_type='text/plain')
        response['Content-Disposition'] = 'filename="text_%s.txt"' %doc_id
        return response
    else:
        req = "SELECT `token`,`num`, `sent_id` FROM `annotator_token` WHERE `doc_id`=%s" %doc_id
        rows = u'Номер предложения в базе данных\tСлово\tНомер слова в предложении\tТэги\tИсправление\tРазметчик\r\n' + u'\r\n'.join(u'\t'.join([str(row[2]),row[0], str(row[1]), '', '', '']) for row in db.execute(req))
        response = HttpResponse(rows, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="tokens_text_%s.txt"' %doc_id
        return response


class Struct:
    def __init__(self, **values):
        vars(self).update(values)


class Index(View):

    def get(self, request, page):
        # эта функция просто достает нужный шаблон и показывает его
        return render_to_response(page + '.html')


class PopUp(View):

    def get(self, request, page):
        page = 'search/' + page + '.html'
        return render(request, page)


class Search(Index):
    # тут все для поиска

    # todo write search
    def get(self, request):  # page does nothing here, just ignore it
        if len(request.GET) < 1:
            # QueryFormset = formset_factory(QueryForm, extra=2)
            return render(request, 'search/search.html')
        else:
            # print request.GET
            # u_groups = request.user.groups
            query = request.GET
            # print(query)
            subcorpus, subcorpus_sents, subcorpus_words, flag = get_subcorpus(query) #, u_groups)
            # print subcorpus.count()
            # subcorpus_sents = [sent.id for doc in subcorpus[0] for sent in doc.sentence_set.all()]
            count_data = {'total_docs': Document.objects.count(),
                          'total_sents': Sentence.objects.count(),
                          'total_tokens': Token.objects.count(),
                          'subcorpus_docs': len(subcorpus),
                          'subcorpus_sents': subcorpus_sents,
                          'subcorpus_words': subcorpus_words}
            per_page = int(query.get(u'per_page'))
            page = request.GET.get('page')
            page = int(page) if page else 1
            expand = int(query.get(u'expand')[-1])
            if "exact_search" in query:
                jq, sent_list, word, res_docs, res_num = exact_full_search(request.GET["exact_word"].lower().encode('utf-8'), subcorpus, flag, expand, page, per_page)
            elif "orig_search" in query:
                jq, sent_list, word, res_docs, res_num = orig_exact_search(request.GET["orig_word"].lower().encode('utf-8'), subcorpus, flag, expand, page, per_page)

            else:
                # todo rewrite this part of search
                jq, sent_list, word, res_docs, res_num = lex_search(query, subcorpus, flag, expand, page, per_page)


            paginator = Paginator(['']*res_num, per_page)
            start = page - 10 if page > 10 else 1
            end = page + 10 if page + 10 <= paginator.num_pages else paginator.num_pages
            paginator.page_range2 = range(start, end+1)
            try:
                sents = paginator.page(page)
            except PageNotAnInteger:
                # If page is not an integer, deliver first page.
                sents = paginator.page(1)
            except EmptyPage:
                # If page is out of range (e.g. 9999), deliver last page of results.
                sents = paginator.page(paginator.num_pages)
            full_path = rePage.sub('', request.get_full_path())
            PREFIX = 'translator_corpus' if PROD else ''
            d_path = full_path.replace(PREFIX + '/search/', PREFIX + '/search/download/')
            if not "orig_search" in query:
                result = sent_dict(sent_list)
                return render(request, 'search/result.html',
                                          {'query': word, 'result': result, 'pages': sents,
                                           'numbers': count_data,
                                           'total': res_num, 'total_docs': res_docs,
                                           'path':full_path, 'd_path':d_path, 'j':jq, 'olstart': (page-1)*per_page + 1})
            else:
                # fw = open('log.txt', 'w')
                # fw.write(str(sent_list))
                # fw.close()
                # for k in sent_list:
                #     print(sent_list[k].text)
                return render(request, 'search/result_orig.html',
                                          {'query': word, 'result': sent_list, 'pages': sents,
                                           'numbers': count_data,
                                           'total': res_num, 'total_docs': res_docs,
                                           'path':full_path, 'd_path':d_path, 'j':jq, 'olstart': (page-1)*per_page + 1})


def sent_dict(sent_list):
    sent_dict = {}

    for sent in sent_list:
        sent.orig = sent.orig.strip('\u2018\u2019\n \r ')
        if sent.orig not in sent_dict:
            sent_dict[sent.orig] = [sent]
        else:
            sent_dict[sent.orig].append(sent)

    return sent_dict