# -*- coding:utf-8 -*-
import torch
import torch.nn as nn
import torch.nn.functional as F
import time
import numpy as np
from torch_scatter import scatter
from collections import defaultdict
# from Refinement import Refinement
from relation_refinement import Refinement,N3
import os
import networkx as nx
os.environ['CUDA_LAUNCH_BLOCKING'] = "1"




class GNNLayer(torch.nn.Module):
    def __init__(self, in_dim, out_dim, attn_dim, n_rel, n_ent, n_node_topk=-1, n_edge_topk=-1, tau=1.0,
                 act=lambda x: x, args=None, emb_model=None):
        super(GNNLayer, self).__init__()
        self.n_rel = n_rel
        self.n_ent = n_ent
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.attn_dim = attn_dim
        self.act = act
        self.n_node_topk = n_node_topk
        self.n_edge_topk = n_edge_topk
        self.tau = tau
        self.rela_embed = emb_model
        self.Ws_attn = nn.Linear(in_dim, attn_dim, bias=False).cuda()
        self.Wr_attn = nn.Linear(in_dim, attn_dim, bias=False).cuda()
        self.Wo_attn     = nn.Linear(in_dim, attn_dim, bias=False).cuda()
        self.Wqr_attn = nn.Linear(in_dim, attn_dim).cuda()
        self.w_alpha = nn.Linear(attn_dim, 1).cuda()
        self.W_h = nn.Linear(in_dim, out_dim, bias=False).cuda()
        self.W_o       = nn.Linear(in_dim, out_dim, bias=False).cuda()
        self.W_samp = nn.Linear(in_dim, 1, bias=False).cuda()

    def train(self, mode=True):
        if not isinstance(mode, bool):
            raise ValueError("training mode is expected to be boolean")
        self.training = mode
        if self.training and self.tau > 0:
            self.softmax = lambda x: F.gumbel_softmax(x, tau=self.tau, hard=False)
        else:
            self.softmax = lambda x: F.softmax(x, dim=1)
        for module in self.children():
            module.train(mode)
        return self

    def forward(self, q_sub, q_rel, hidden, edges, nodes, old_nodes_new_idx, batchsize):
        # edges: [N_edge_of_all_batch, 6]
        # with (batch_idx, head, rela, tail, head_idx, tail_idx)
        # note that head_idx and tail_idx are relative index
        sub = edges[:, 4]
        rel = edges[:, 2]
        obj = edges[:, 5]
        hs = hidden[sub]
        hr = self.rela_embed(edges[:, 1], rel)

        r_idx = edges[:, 0]

        h_qr = self.rela_embed(q_sub, q_rel)[r_idx]
        n_node = nodes.shape[0]
        message = hs + hr

        ho = self.rela_embed.lhs(edges[:, 3])

        # sample edges w.r.t. alpha
        if self.n_edge_topk > 0:
            alpha = self.w_alpha(nn.ReLU()(self.Ws_attn(hs) + self.Wr_attn(hr) + self.Wqr_attn(h_qr))).squeeze(-1)
            edge_prob = F.gumbel_softmax(alpha, tau=1, hard=False)
            topk_index = torch.argsort(edge_prob, descending=True)[:self.n_edge_topk]
            edge_prob_hard = torch.zeros((alpha.shape[0])).cuda()
            edge_prob_hard[topk_index] = 1
            alpha *= (edge_prob_hard - edge_prob.detach() + edge_prob)
            alpha = torch.sigmoid(alpha).unsqueeze(-1)

        else:
            alpha = torch.sigmoid(self.w_alpha(nn.ReLU()(self.Ws_attn(hs) + self.Wr_attn(hr) + self.Wqr_attn(
                h_qr))))  # [N_edge_of_all_batch, 1]

        # aggregate message and then propagate
        message = alpha * message  # [rel.shape[0],dim]
        message_agg = scatter(message, index=obj, dim=0, dim_size=n_node, reduce='sum')  # [n_node, dim]

        hidden_new = self.act(self.W_h(message_agg))  # [n_node, dim]

        hidden_new = hidden_new.clone()

        # forward without node sampling
        if self.n_node_topk <= 0:
            return hidden_new,nodes,torch.ones(n_node).bool()

        # forward with node sampling
        # indexing sampling operation
        tmp_diff_node_idx = torch.ones(n_node)
        tmp_diff_node_idx[old_nodes_new_idx] = 0
        bool_diff_node_idx = tmp_diff_node_idx.bool()
        diff_node = nodes[bool_diff_node_idx]

        # project logit to fixed-size tensor via indexing
        diff_node_logit = self.W_samp(hidden_new[bool_diff_node_idx]).squeeze(-1)  # [all_batch_new_nodes]

        # save logit to node_scores for later indexing
        node_scores = torch.ones((batchsize, self.n_ent)).cuda() * float('-inf')
        node_scores[diff_node[:, 0], diff_node[:, 1]] = diff_node_logit  # [batchsize, n_ent]

        # select top-k nodes
        # (train mode) self.softmax == F.gumbel_softmax
        # (eval mode)  self.softmax == F.softmax
        node_scores = self.softmax(node_scores)  # [batchsize, n_ent]
        topk_index = torch.topk(node_scores, self.n_node_topk, dim=1).indices.reshape(-1)
        topk_batchidx = torch.arange(batchsize).repeat(self.n_node_topk, 1).T.reshape(-1)
        batch_topk_nodes = torch.zeros((batchsize, self.n_ent)).cuda() # [batchsize, n_ent]
        batch_topk_nodes[topk_batchidx, topk_index] = 1

        # get sampled nodes' relative index
        bool_sampled_diff_nodes_idx = batch_topk_nodes[diff_node[:, 0], diff_node[:,1]].bool()  # diff_node    diff_node[:,0]:batch_idx  diff_node[:,1]:node_idx
        bool_same_node_idx = ~bool_diff_node_idx.cuda()
        bool_same_node_idx[bool_diff_node_idx] = bool_sampled_diff_nodes_idx

        # update node embeddings
        diff_node_prob_hard = batch_topk_nodes[diff_node[:, 0], diff_node[:, 1]]
        diff_node_prob = node_scores[diff_node[:, 0], diff_node[:, 1]]
        hidden_new[bool_diff_node_idx] *= (diff_node_prob_hard - diff_node_prob.detach() + diff_node_prob).unsqueeze(
            -1)

        # extract sampled nodes an their embeddings
        new_nodes = nodes[bool_same_node_idx]  # with batch_idx,node_idx
        hidden_new = hidden_new[bool_same_node_idx]  # [n_node, rdim]

        return hidden_new, new_nodes, bool_same_node_idx





class GNNModel(torch.nn.Module):
    def __init__(self, params, loader):
        super(GNNModel, self).__init__()

        self.n_layer = params.n_layer
        self.hidden_dim = params.hidden_dim
        self.attn_dim = params.attn_dim
        self.n_ent = params.n_ent
        self.n_rel = params.n_rel
        self.n_node_topk = params.n_node_topk
        self.n_edge_topk = params.n_edge_topk
        self.loader = loader
        self.lossflag = params.lossflag
        if params.lossflag:
            self.regFlag     = params.Flag
            reg              = params.reg
            self.regularizer = N3(reg)
        self.rela_embed = Refinement((params.n_ent, 2 * self.n_rel + 1), params.hidden_dim, params.rdim, 'LSTMCell',
                                 params.init)
        self.pre_embed=  self.rela_embed.lhs.weight


        acts = {'relu': nn.ReLU(), 'tanh': torch.tanh, 'idd': lambda x: x}
        act = acts[params.act]
        self.act = nn.ReLU()

        self.gnn_layers = []
        for i in range(self.n_layer):
            i_n_node_topk = self.n_node_topk if 'int' in str(type(self.n_node_topk)) else self.n_node_topk[i]
            self.gnn_layers.append(GNNLayer(self.hidden_dim, self.hidden_dim, self.attn_dim, self.n_rel, self.n_ent, \
                                            n_node_topk=i_n_node_topk, n_edge_topk=self.n_edge_topk, tau=params.tau,
                                            act=act, args=params, emb_model=self.rela_embed))

        self.gnn_layers = nn.ModuleList(self.gnn_layers)
        self.dropout = nn.Dropout(params.dropout)
        self.W_final = nn.Linear(self.hidden_dim, 1, bias=False).cuda()
        self.lamda=params.lamda
        if (params.gate == 'GRU'):
            self.gate = nn.GRU(self.hidden_dim, self.hidden_dim).cuda()
        else:
            self.gate = nn.LSTM(self.hidden_dim, self.hidden_dim)

    def updateTopkNums(self, topk_list):
        assert len(topk_list) == self.n_layer
        for idx in range(self.n_layer):
            self.gnn_layers[idx].n_node_topk = topk_list[idx]

    def fixSamplingWeight(self):
        def freeze(m):
            m.requires_grad = False

        for i in range(self.n_layer):
            self.gnn_layers[i].W_samp.apply(freeze)



    def forward(self, subs, rels, mode='train'):
        n = len(subs)  # n == B (Batchsize)
        q_sub = torch.LongTensor(subs).cuda()  # [B]
        q_rel = torch.LongTensor(rels).cuda()  # [B]
        nodes = torch.cat([torch.arange(n).unsqueeze(1).cuda(), q_sub.unsqueeze(1)],
                          1)  # [B, 2] with (batch_idx, node_idx)
        hidden = self.pre_embed[q_sub]  # [B, dim]
        hidden = self.dropout(hidden)#++++++++++++++++++++++++++++++++
        h0 = hidden.unsqueeze(0)  # [1, B, dim]

        self.edges_per_layer = {batch_idx: [] for batch_idx in range(n)}
        for i in range(self.n_layer):
            # layers with sampling
            # nodes (of i-th layer): [k1, 2]
            # edges (of i-th layer): [k2, 6]
            # old_nodes_new_idx (of previous layer): [k1']
            nodes, edges, old_nodes_new_idx = self.loader.get_neighbors(nodes.data.cpu().numpy(), n,
                                                                        mode=mode)
            n_node = nodes.size(0)
            # old_nodes = nodes

            # GNN forward -> get hidden representation at i-th layer
            # hidden: [k1, dim]
            hidden, nodes, sampled_nodes_idx = self.gnn_layers[i](q_sub, q_rel, hidden, edges, nodes, old_nodes_new_idx,n)

            # combine h0 and hi -> update hi with gate operation
            h0 = torch.zeros(1, n_node, hidden.size(1)).cuda().index_copy_(1, old_nodes_new_idx,
                                                                           h0)
            h0 = h0[0, sampled_nodes_idx, :].unsqueeze(0)
            hidden = self.dropout(hidden)
            hidden, h0 = self.gate(hidden.unsqueeze(0), h0)
            hidden = hidden.squeeze(0)


        lhs_rel = self.rela_embed.lhs.weight[q_sub]*self.rela_embed(q_sub, q_rel)  # [B, dim]
        

        rhs = self.rela_embed.rhs.weight[nodes[:, 1]]  # nodes[:, 1] shape: (num_nodes, embed_dim)

        batch_idx = nodes[:, 0]

        lhs_rel_expanded = lhs_rel[batch_idx]  # shape: (num_nodes, embed_dim)


        selected_scores_tensor = torch.sum(lhs_rel_expanded * rhs, dim=1)  # shape: (num_nodes,)

        # readout
        # [K, 2] (batch_idx, node_idx) K is w.r.t. n_nodes
        scores = self.W_final(hidden).squeeze(-1)

        scores = self.lamda * scores + (1 - self.lamda) * selected_scores_tensor

        # non-visited entities.txt have 0 scores
        scores_all = torch.zeros((n, self.loader.n_ent)).cuda()
        # [B, n_all_nodes]
        scores_all[[nodes[:, 0], nodes[:, 1]]] = scores

        lo = 0
        if  mode == 'train' and self.lossflag:
            lo = self.regularizer((self.rela_embed.lhs.weight, self.rela_embed.rel.weight,self.rela_embed.rhs.weight))

        if mode == 'train':
            return scores_all, lo
        else:
            return scores_all