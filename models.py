import torch.nn as nn
from torch.autograd import Variable
import torch
import math
import random
import itertools
import numpy as np
from datetime import datetime
import hashlib
import os,sys
from copy import deepcopy as copy
wd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.append(os.path.join(wd,"scripts","data"))
from data.batch_scheduler import PatientRecord

# Three functions that are used to get the age encoding functions
def time_index(i,pos,d=512,c=10000):
	if i % 2 == 0:
		v = math.sin(pos/(c**(2*i/d)))
	else:
		v = math.cos(pos/(c**(2*i/d)))
	return v

def get_age_arr(age,max_age=120.0,d=512):
	arr = np.zeros((d,))
	pos = int(age / max_age * 1000)
	for i in range(d):
		arr[i] = time_index(i,pos,d)
	return arr

def get_age_encoding(date,birthdate,d=512):
	age = date.year - birthdate.year
	return get_age_arr(age,d=d)

# https://stackoverflow.com/questions/26685067/represent-a-hash-string-to-binary-in-python
# Two functions to encode strings as binary arrays
def text_to_bin(text, n_bin=32,d=512):
	if text is None: text=""
	text=text.lower()
	word_ord = '{}'.format(bin(int(hashlib.md5(text.encode('utf-8')).hexdigest(), n_bin)))
	word_ord = word_ord[2:]
	arr = []
	for i in range(d):
		a = word_ord[i % len(word_ord)]
		if a == "1":
			arr.append(1.0)
		elif a == "0":
			arr.append(0.0)
		else:
			raise Exception("%s is bad"% str(a))
	return arr

def encode_static_inputs(static_input,d=512):
	arr = np.zeros((len(static_input),d))
	for i in range(len(static_input)):
		arr[i,:] = text_to_bin(static_input[i],d=d)
	return arr

class Reshape(nn.Module):
	'''
		Used in a nn.Sequential pipeline to reshape on the fly.
	'''
	def __init__(self, *target_shape):
		super().__init__()
		self.target_shape = target_shape
	
	def forward(self, x):
		return x.view(*self.target_shape)

class NeuralNetwork(nn.Module):
    def __init__(self,inp,outp):
        super().__init__()
        self.flatten = nn.Flatten()
        self.linear_relu_stack = nn.Sequential(
            nn.Linear(inp, 4096),
            nn.LeakyReLU(),
			nn.BatchNorm1d(4096),
            nn.Linear(4096, 2048),
            nn.LeakyReLU(),
			nn.BatchNorm1d(2048),
			nn.Dropout(0.5),
			nn.Linear(2048, 1024),
			nn.LeakyReLU(),
			nn.BatchNorm1d(1024),
			nn.Linear(1024, 512),
			nn.LeakyReLU(),
			nn.BatchNorm1d(512),
			nn.Linear(512, 256),
			nn.LeakyReLU(),
			nn.BatchNorm1d(256),
            nn.Linear(256, 64),
			nn.LeakyReLU(),
			nn.BatchNorm1d(64),
			nn.Dropout(0.5),
            nn.Linear(64, outp),
			nn.Sigmoid(),
        )
    def forward(self, x):
        x = self.flatten(x)
        logits = self.linear_relu_stack(x)
        return logits

class Encoder(nn.Module):
	def __init__(self,LATENT_SIZE=512):
		super(Encoder,self).__init__()
		nchan=1
		base_feat = 64
		self.encoder = nn.Sequential(
			nn.Conv3d(in_channels = nchan, out_channels = base_feat, stride=2,
				kernel_size=5, padding = 2), #1*96*96*96 -> 64*48*48*48
			nn.LeakyReLU(),
			#nn.InstanceNorm3d(base_feat),
			nn.Conv3d(in_channels = base_feat, out_channels = base_feat*2,
				stride=2, kernel_size = 5,
				padding=2), #64*48*48*48 -> 128*24*24*24
			nn.LeakyReLU(),
			nn.InstanceNorm3d(base_feat*2),
			nn.Conv3d(in_channels = base_feat*2, out_channels = base_feat*4,
				stride=2,kernel_size = 3,
				padding=1), #128*24*24*24 -> 256*12*12*12
			nn.LeakyReLU(),
			#nn.InstanceNorm3d(base_feat*4),
			nn.Conv3d(in_channels = base_feat*4, out_channels = base_feat*4,
				stride=4,kernel_size = 5,padding=2), #256*12*12*12 -> 256*3*3*3
			nn.LeakyReLU(),
			nn.InstanceNorm3d(base_feat*4),
			nn.Conv3d(in_channels = base_feat*4, out_channels = base_feat*32,
				stride=1,kernel_size = 3,padding=0), #256*3*3*3 -> 2048*1*1*1
			nn.LeakyReLU(),
			Reshape([-1,base_feat*32]),
			nn.Linear(in_features = base_feat*32, out_features = base_feat*16),
			nn.LeakyReLU(),
			nn.Linear(in_features = base_feat*16, out_features = LATENT_SIZE),
			
		)
	def parameters(self):
		return self.encoder.parameters()
	def forward(self, x):
		x = self.encoder(x)
		return x


class Decoder(nn.Module):
	def __init__(self):
		super(Decoder,self).__init__()
		nchan=1
		base_feat = 64
		LATENT_SIZE = 512	
		self.decoder = nn.Sequential(
			nn.Linear(in_features = LATENT_SIZE, out_features = base_feat*16),
			nn.ReLU(),
			nn.BatchNorm1d(base_feat*16),
			nn.Linear(in_features = base_feat*16, out_features = base_feat*32),
			nn.ReLU(),
			Reshape([-1,base_feat*32,1,1,1]),
			nn.ConvTranspose3d(in_channels = base_feat*32,
				out_channels = base_feat*16, kernel_size = 3,stride=1,
				padding=0), #2048*1*1*1 -> 1024*3*3*3
			nn.ReLU(),
			nn.BatchNorm3d(base_feat*16),
			nn.ConvTranspose3d(in_channels = base_feat*16,
				out_channels = base_feat*4, kernel_size = 4,stride=2, padding=1,
				bias=False), #256*3*3*3 -> 256*6*6*6
			nn.ReLU(),
			nn.BatchNorm3d(base_feat*4),
			nn.ConvTranspose3d(in_channels = base_feat*4,
				out_channels = base_feat*4, kernel_size = 4,stride=2, padding=1,
				bias=False), #256*6*6*6 -> 256*12*12*12
			nn.ReLU(),
			nn.BatchNorm3d(base_feat*4),
			nn.ConvTranspose3d(in_channels = base_feat*4,
				out_channels = base_feat*2, kernel_size = 4,stride=2, padding=1,
				bias=False), #256*12*12*12 -> 128*24*24*24
			nn.ReLU(),
			nn.BatchNorm3d(base_feat*2),
			nn.ConvTranspose3d(in_channels = base_feat*2,
				out_channels = base_feat, kernel_size = 4, stride=2, padding=1,
				bias=False), #128*24*24*24 -> 64*48*48*48
			nn.ReLU(),
			nn.BatchNorm3d(base_feat),
			nn.ConvTranspose3d(in_channels = base_feat, out_channels = 1,
				kernel_size = 4,stride=2,padding=1,
				bias=False), #64*48*48*48 -> 1*96*96*96
			nn.Sigmoid()
		)
	def forward(self, x):
		x = self.decoder(x)
		return x

class Regressor(nn.Module):
	def __init__(self,latent_dim,n_confounds,n_choices,device='cpu'):
		super(Regressor,self).__init__()
		base_feat = 64
		n = 4
		self.regressor_set = []
		for _ in range(n_choices):
			self.regressor_set.append(
				nn.Sequential(
					nn.Linear(latent_dim,base_feat*n),
					nn.LeakyReLU(),
					Reshape([-1,n,base_feat]),
					nn.InstanceNorm1d(n,affine=True),
					Reshape([-1,n*base_feat]),
					
					nn.Linear(base_feat*n,base_feat*n),
					nn.LeakyReLU(),
					Reshape([-1,n,base_feat]),
					nn.InstanceNorm1d(n,affine=True),
					Reshape([-1,n*base_feat]),
						
					nn.Linear(base_feat*n,base_feat*n),
					nn.LeakyReLU(),
					Reshape([-1,n,base_feat]),
					nn.InstanceNorm1d(n,affine=True),
					Reshape([-1,n*base_feat]),
			
					nn.Linear(n*base_feat,n_confounds),
					#nn.ReLU(),
					Reshape([-1,n_confounds,1]),
					nn.Sigmoid()
				)
			)
		#self.regressor_set = torch.cat(self.regressor_set,2)
		#self.regressor_set = [copy(self.regressor1).to(device) for _ in range(n_choices)]
		#self.regressor = nn.Sequential(
		#	nn.Linear(latent_dim,base_feat*8),
		#	nn.LeakyReLU(),
		#	Reshape([-1,8,base_feat]),
		#	nn.InstanceNorm1d(8,affine=True),
		#	Reshape([-1,8*base_feat]),
			
		#	nn.Linear(base_feat*8,base_feat*8),
		#	nn.LeakyReLU(),
		#	Reshape([-1,8,base_feat]),
		#	nn.InstanceNorm1d(8,affine=True),
		#	Reshape([-1,8*base_feat]),
			
			#nn.Linear(base_feat*8,base_feat*8),
			#nn.LeakyReLU(),
			#Reshape([-1,8,base_feat]),
			#nn.InstanceNorm1d(8,affine=True),
			#Reshape([-1,8*base_feat]),
			
		#	nn.Linear(base_feat*8,base_feat*8),
		#	nn.LeakyReLU(),
		#	#Reshape([-1,8,base_feat]),
		#	#nn.InstanceNorm1d(8,affine=True),
		#	#Reshape([-1,8*base_feat]),
			
		#	nn.Linear(base_feat*8,n_confounds*n_choices),
		#	Reshape([-1,n_confounds,n_choices]),
		#	nn.Sigmoid()
		#)
	def parameters(self):
		return itertools.chain(*[r.parameters() for r in self.regressor_set])
	def cuda(self,device):
		for r in self.regressor_set:
			r.cuda(device)
	def cpu(self):
		for r in self.regressor_set:
			r.cpu()
	def forward(self,x):
		return torch.cat([r(x) for r in self.regressor_set],2)
		
		#return self.regressor_set(x)

class Classifier(nn.Module):
	def __init__(self,latent_dim,n_inputs,base_feat,nout,nlabels):
		super(Classifier,self).__init__()
		
		self.classifier = nn.Sequential(
			#nn.Conv2d(in_channels = 1, out_channels = base_feat*4,
			# stride=1, kernel_size=(self.LATENT_DIM,1),
			# padding =0), #1*96*96*96 -> 64*48*48*48
			#Reshape([-1,self.LATENT_DIM*self.n_inputs]),
			nn.Linear(latent_dim*n_inputs,n_inputs*base_feat*4),
			nn.LeakyReLU(),
			Reshape([-1,n_inputs,base_feat*4]),
			nn.InstanceNorm1d(n_inputs,affine=True),
			Reshape([-1,1,n_inputs*base_feat*4]),

			nn.Linear(in_features = n_inputs*base_feat*4,
				out_features = n_inputs*base_feat*2),
			nn.LeakyReLU(),
			Reshape([-1,n_inputs,base_feat*2]),
			nn.InstanceNorm1d(n_inputs,affine=True),
			Reshape([-1,1,n_inputs*base_feat*2]),

			#nn.Linear(in_features = n_inputs*base_feat*2,
			#	out_features = n_inputs*base_feat*2),
			#nn.LeakyReLU(),
			#Reshape([-1,n_inputs,base_feat*2]),
			#nn.InstanceNorm1d(n_inputs,affine=True),
			#Reshape([-1,1,n_inputs*base_feat*2]),

			#nn.Linear(in_features = n_inputs*base_feat*2,
			#	out_features = n_inputs*base_feat),
			#nn.LeakyReLU(),
			#Reshape([-1,n_inputs,base_feat]),
			#nn.InstanceNorm1d(n_inputs,affine=True),
			#Reshape([-1,1,n_inputs*base_feat]),

			nn.Linear(in_features = n_inputs*base_feat*2, out_features = nout*nlabels),
			Reshape([-1,nout,nlabels]),
			#nn.ReLU()
			nn.Sigmoid(),	
		)
	def parameters(self):
		return self.classifier.parameters()
	def forward(self,x):
		return self.classifier(x)

class MultiInputModule(nn.Module):
	def __init__(self,
				n_dyn_inputs = 14,
				n_stat_inputs = 2,
				n_out = 4,
				use_attn = False,
				encode_age = False,
				regressor_dims = None,
				variational = False,
				zero_input = False,
				remove_uncertain = False,
				device = torch.device('cpu')):
		super(MultiInputModule,self).__init__()
		
		# Model Parameters
		self.LATENT_DIM = 128
		self.n_stat_inputs = n_stat_inputs
		self.n_dyn_inputs = n_dyn_inputs
		self.n_inputs = self.n_stat_inputs + self.n_dyn_inputs
		base_feat = 64
		self.nout = n_out
		num_heads = self.n_inputs
		embed_dim = self.LATENT_DIM
		self.regressor_dims = regressor_dims
		self.zero_input = zero_input
		self.remove_uncertain = remove_uncertain
		if self.remove_uncertain:
			self.record_training_sample = False
			self.num_training_samples = 300
			self.training_sample = torch.zeros(
				(
					self.LATENT_DIM,
					self.num_training_samples
				),
				device=device)

		if isinstance(n_out,int):
			n_out = (n_out,1)
		# Training options
		self.use_attn = use_attn
		self.encode_age = encode_age
		self.static_dropout = True # Randomly mask static inputs in training
		self.variational = variational
		
		# A record that prevents unrecognized keys from being applied during
		# the test phase
		self.static_record = [set() for _ in range(self.n_stat_inputs)]
		
		# Modules
		
		# Makes the encoder output a variational latent space, so it's a
		# Gaussian distribution.
		if self.variational:
			self.encoder = Encoder(LATENT_SIZE=2*self.LATENT_DIM)
			self.z_mean = nn.Sequential(
				nn.Linear(2*self.LATENT_DIM,self.LATENT_DIM)
			)
			self.z_log_sigma = nn.Sequential(
				nn.Linear(2*self.LATENT_DIM,self.LATENT_DIM)
			)
			self.epsilon = torch.distributions.Normal(0, 1)
			self.epsilon.loc = self.epsilon.loc.cuda(device)
			self.epsilon.scale = self.epsilon.scale.cuda(device)
		else:
			self.encoder = Encoder(LATENT_SIZE=self.LATENT_DIM)
		#self.decoder = Decoder()
		if self.use_attn:
			self.multihead_attn = nn.MultiheadAttention(
										embed_dim,
										num_heads,
										batch_first=True)
		self.classifier = Classifier(self.LATENT_DIM,
										self.n_inputs,
										base_feat,
										self.nout[0],
										self.nout[1])
		if self.regressor_dims is not None:
			n_confounds,n_choices = self.regressor_dims
			self.regressor = Regressor(self.LATENT_DIM,n_confounds,n_choices,
				device=device)
		else: self.regressor = None
	
	def forward_ensemble(self,kwargs,n_ens=10):
		x = []
		for i in range(n_ens):
			x.append(self(**kwargs))
		return x

	def cuda(self,device):
		self.device=device
		if self.variational:
			self.epsilon.loc = self.epsilon.loc.cuda(device)
			self.epsilon.scale = self.epsilon.scale.cuda(device)
		self.regressor.cuda(device)
		return super().cuda(device)
	def cpu(self):
		self.device = torch.device('cpu')
		if self.variational:
			self.epsilon.loc = self.epsilon.loc.cpu()
			self.epsilon.scale = self.epsilon.scale.cpu()
		self.regressor.cpu()
		return super().cpu()
	
	def regressor_freeze(self):
		for param in self.classifier_parameters():
			param.requires_grad = True
		for param in self.regressor.parameters():
			param.requires_grad = False
			
	def classifier_freeze(self):
		for param in self.classifier_parameters():
			param.requires_grad = False
		for param in self.regressor.parameters():
			param.requires_grad = True

	def classifier_parameters(self):
		if self.variational:
			return itertools.chain(self.encoder.parameters(),
				self.classifier.parameters(),
				self.z_log_sigma.parameters(),
				self.z_mean.parameters())
		else:
			return itertools.chain(self.encoder.parameters(),
				self.classifier.parameters())

	def forward(self,
				x,
				static_input=None,
				dates=None,
				bdate=None,
				return_regress = False,
				return_encoded = False,
				encoded_input = False):
		if not encoded_input:
			if isinstance(x,PatientRecord):
				static_inputs = [_[0] for _ in x.extra_info_list]
				dates = x.dates
				bdate = x.bdate
				x = x.X
			if (self.encode_age and (dates is None or bdate is None)):
				raise Exception("Need dates as input to encode age")
			if x.size(0) > self.n_dyn_inputs:
				raise Exception(
					"Max dynamic inputs is %d. Received %d. Reduce batch size." % \
						(
							self.n_dyn_inputs,
							int(x.size(0))
						)
					)
			
			if static_input is not None and len(static_input) > self.n_stat_inputs:
				raise Exception(
					"Max dynamic inputs is %d. Received %d. Reduce batch size." % \
						(
							self.n_stat_inputs,
							len(static_input)
						)
					)
			
			# Encode everything - separate batches
			if self.variational:
				x = self.encoder(x)
				z_mean = self.z_mean(x)
				z_log_sigma = self.z_log_sigma(x)
				x = z_mean + (z_log_sigma.exp()*self.epsilon.sample(z_mean.shape))
				self.kl = (z_mean**2 + z_log_sigma.exp()**2 - z_log_sigma-0.5).mean()
			else:
				x = self.encoder(x) # [1-16]*96*96*96 -> [1-16]*512
			
			if hasattr(self,'remove_uncertain'):
				if self.remove_uncertain:
					if self.record_training_sample:
						self.training_sample[:,self.training_i:min(self.training_i + x.shape[0],self.num_training_samples)] = x
						self.training_i += x.shape[0]
						if self.training_i >= self.num_training_samples:
							self.record_training_sample = False
				
			
			if return_encoded:
				return x

		use_regression = hasattr(self,'regressor') and \
			(self.regressor is not None) and \
			(self.training or return_regress)
		if use_regression:
			reg = self.regressor(x)
		x = torch.unsqueeze(x,0)
		
		
		# Encode dynamic inputs with dates using positional encoding
		if (self.encode_age):
			age_encodings = []
			for i,date in enumerate(dates):
				age_encoding = get_age_encoding(date,bdate,d=self.LATENT_DIM)
				age_encodings.append(age_encoding)
			age_encodings = np.array(age_encodings)
			age_encodings = torch.tensor(age_encodings,device=x.get_device()).float()
			x = torch.add(x,age_encodings)
		
		# Pad encodings with zeros, depending on input size
		e_size = list(x.size())
		e_size[1] = self.n_inputs - e_size[1] #[1-16]*512 -> 16*512
		if (not hasattr(self,'zero_input')) or self.zero_input:
			x = torch.cat((x,torch.zeros(e_size,device=x.get_device())),axis=1)
		else:
			x_ = torch.clone(x)
			while x_.size()[1] < e_size[1]:
				x_ = torch.cat((x_,torch.clone(x_)),axis=1)
			x_ = x_[:,:e_size[1],:]
			x = torch.cat((x,x_),axis=1)
			#x = x.repeat(1,(e_size[1]*2 // x.size()[1]),1)[:,:e_size[1],:]
		
		# Place static inputs near end
		if static_input is not None:
			if len(static_input) != self.n_stat_inputs:
				raise Exception(
						"Received %d static inputs, but it is set at %d" % \
						(len(static_input),self.n_stat_inputs)
					)
			if self.training:
				for i,e in enumerate(static_input):
					self.static_record[i].add(e)
			else:
				for i,e in enumerate(self.static_record):
					if static_input[i] not in e:
						raise Exception(
							"""
								Input %s not a previous demographic
								input (previous inputs were %s)
							""" % (static_input[i],str(e))
						)
			x_ = encode_static_inputs(static_input,d=self.LATENT_DIM)
			x_ = torch.tensor(x_,device = x.get_device())
			x_ = torch.unsqueeze(x_,0)
			for i in range(x_.shape[0]):
				if ((not self.static_dropout) or random.choice([True,False]) and self.training) or\
					(not self.static_dropout):
					x[:,(-(self.n_stat_inputs) + i):,:] = x_[i,:]
		
		# Randomly order dynamic encodings
		r = list(range(self.n_inputs))
		r_ = r[:self.n_stat_inputs]
		random.shuffle(r_)
		r[:self.n_stat_inputs] = r_
		x = x[:,r,...]
		
		# Apply attention mask
		if self.use_attn:
			m = torch.cat(
					(
						torch.zeros(
							list(x.size())[:],
							device=x.get_device(),
							dtype=torch.bool),
						torch.ones(
							e_size[:],
							device=x.get_device(),
							dtype=torch.bool)
					),
				axis=1)
			m = m[:,r,...]
			x,_ = self.multihead_attn(x,x,x,need_weights=False)#,attn_mask=m)

		#x = self.encoder(x)
		#x = torch.flatten(x, start_dim=1)
		#z_mean = self.z_mean(x)
		#z_log_sigma = self.z_log_sigma(x)
		#z = z_mean + (z_log_sigma.exp()*self.epsilon.sample(z_mean.shape))
		#z = nn.functional.sigmoid(z)
		#y = self.decoder(z)
		#self.kl = (z_mean**2 + z_log_sigma.exp()**2 - z_log_sigma - 0.5).sum()
	
		# Switch batch channel with layer channel prior to running classifier
		x = torch.unsqueeze(x,-1)
		x = x.contiguous().view([-1,1,self.LATENT_DIM*self.n_inputs]) # 16*512 -> 1*[16*512]
		x = self.classifier(x)
		if use_regression: return x,reg
		else: return x

class RNN(nn.Module):
	def __init__(self,ninp,nhid,nout,nlayers=5,dropout=0.3):
		super(RNN, self).__init__()
		print("ninp: %d" % ninp)
		print("nhid: %d" % nhid)
		print("nout: %d" % nout)
		print("nlayers: %d" % nlayers)
		latent_dim = 64
		#self.rnn = nn.RNN(ninp, nhid, num_layers = nlayers, dropout=dropout,batch_first=True)
		self.rnn = nn.Sequential(
			nn.Linear(nhid+ninp,latent_dim*4),
			nn.LeakyReLU(negative_slope=0.3,inplace=True),
			#nn.BatchNorm1d(1,affine=True),
			#nn.Dropout(dropout),
			#nn.Linear(256,256),
			#nn.LeakyReLU(negative_slope=0.3,inplace=True),
			#nn.BatchNorm1d(1,affine=True),
			#nn.Dropout(dropout),
			nn.Linear(latent_dim*4,latent_dim*4),
			nn.LeakyReLU(negative_slope=0.3,inplace=True),
			nn.BatchNorm1d(1,affine=True),
			nn.Linear(latent_dim*4,nhid),
		)
		self.classifier = nn.Sequential(
			nn.Linear(nhid,nout),
			nn.LeakyReLU(negative_slope=0.3,inplace=True),
		)
	def forward(self,input,hidden):
		#print("input.size(): %s"%str(input.size()))
		#print("hidden.size(): %s"%str(hidden.size()))
		#hidden = torch.swapaxes(hidden,0,1)
		#output, hidden = self.rnn(input, hidden)
		#print("output.size(): %s"%str(output.size()))
		#print("RNN")
		#print("1: input.size(): %s" % str(input.size()))
		#print("2: hidden.size(): %s" % str(hidden.size()))
		hidden = self.rnn(torch.cat((input,hidden),-1))
		#print("3: hidden.size(): %s" % str(hidden.size()))
		output = self.classifier(hidden)
		#print("4: output.size(): %s" % str(output.size()))
		return output,hidden

class RNNModel(nn.Module):
	"""Container module with an encoder, a recurrent module, and a decoder."""

	def __init__(self, rnn_type, ntoken, ninp, nhid, nlayers, dropout=0.5, tie_weights=False):
		super(RNNModel, self).__init__()
		self.drop = nn.Dropout(dropout)
		self.encoder = nn.Embedding(ntoken, ninp)
		if rnn_type in ['LSTM', 'GRU']:
			self.rnn = getattr(nn, rnn_type)(ninp, nhid, nlayers, dropout=dropout)
		else:
			try:
				nonlinearity = {'RNN_TANH': 'tanh', 'RNN_RELU': 'relu'}[rnn_type]
			except KeyError:
				raise ValueError( """An invalid option for `--model` was supplied,
								 options are ['LSTM', 'GRU', 'RNN_TANH' or 'RNN_RELU']""")
			self.rnn = nn.RNN(ninp, nhid, nlayers, nonlinearity=nonlinearity, dropout=dropout)
		self.decoder = nn.Linear(nhid, ntoken)

		# Optionally tie weights as in:
		# "Using the Output Embedding to Improve Language Models" (Press & Wolf 2016)
		# https://arxiv.org/abs/1608.05859
		# and
		# "Tying Word Vectors and Word Classifiers: A Loss Framework for Language Modeling" (Inan et al. 2016)
		# https://arxiv.org/abs/1611.01462
		if tie_weights:
			if nhid != ninp:
				raise ValueError('When using the tied flag, nhid must be equal to emsize')
			self.decoder.weight = self.encoder.weight

		self.init_weights()

		self.rnn_type = rnn_type
		self.nhid = nhid
		self.nlayers = nlayers

	def init_weights(self):
		initrange = 0.1
		self.encoder.weight.data.uniform_(-initrange, initrange)
		self.decoder.bias.data.fill_(0)
		self.decoder.weight.data.uniform_(-initrange, initrange)

	def forward(self, input, hidden):
		emb = self.drop(self.encoder(input))
		output, hidden = self.rnn(emb, hidden)
		output = self.drop(output)
		decoded = self.decoder(output.view(output.size(0)*output.size(1), output.size(2)))
		return decoded.view(output.size(0), output.size(1), decoded.size(1)), hidden

	def init_hidden(self, bsz):
		weight = next(self.parameters()).data
		if self.rnn_type == 'LSTM':
			return (Variable(weight.new(self.nlayers, bsz, self.nhid).zero_()),
					Variable(weight.new(self.nlayers, bsz, self.nhid).zero_()))
		else:
			return Variable(weight.new(self.nlayers, bsz, self.nhid).zero_())

class CombinedModel(nn.Module):
	def __init__(self,ninp,nhid,nout,output_dim,nlayers=5,dropout=0.5):
		super(CombinedModel, self).__init__()
		self.Encoder = Encoder(output_dim)
		self.RNN = RNN(ninp,nhid,nout,nlayers=nlayers,dropout=dropout)
	def forward(self,input,hidden):
		#print("Combined Model")
		#print("1: input.size(): %s" % str(input.size()))
		#print("2: hidden.size(): %s" % str(hidden.size()))
		if len(input.size()) == 5:
			x = self.Encoder(input)
			#print("3: x.size(): %s" % str(x.size()))
			x = torch.unsqueeze(x,1).float()
		else:
			x = input
		#print("4: x.size(): %s" % str(x.size()))
		output,hidden = self.RNN(x,hidden)
		#print("5: hidden.size(): %s" % str(hidden.size()))
		#print("6: output.size(): %s" % str(output.size())) 
		return output,hidden
	def freeze_encoder(self):
		self.Encoder.weight.requires_grad = False
		self.Encoder.bias.requires_grad = False
	def unfreeze_encoder(self):
		self.Encoder.weight.requires_grad = True
		self.Encoder.bias.requires_grad = True

class EnsembleModel(nn.Module):
	def __init__(self,model_list):
		super(EnsembleModel,self).__init__()
		self.models = [torch.load(_) for _ in model_list]
		for model in self.models: model.eval()
	def forward(self,input,hidden):
		new_output = []
		new_hidden = []
		for i in range(hidden.size()[3]):
			model = self.models[i]
			#print("input.size(): %s" % str(input.size()))
			x = model.Encoder(input)
			x = torch.unsqueeze(x,1).float()
			output,h = model.RNN(x,hidden[...,i])
			new_output.append(torch.unsqueeze(output,3))
			new_hidden.append(torch.unsqueeze(h,3))
			#print("output.size(): %s" % str(output.size()))
			#print("h.size(): %s" % str(h.size()))
		new_output = torch.cat(new_output,dim=3)
		new_hidden = torch.cat(new_hidden,dim=3)
		return new_output,new_hidden

class VariationalEncoder(nn.Module):
	def __init__(self):
		super(Encoder,self).__init__()
		nchan=1
		base_feat = 64
		LATENT_SIZE = 512
		self.encoder = nn.Sequential(
			nn.Conv3d(in_channels = nchan, out_channels = base_feat, stride=2, kernel_size=5, padding = 2), #1*96*96*96 -> 64*48*48*48
			nn.LeakyReLU(),
			nn.Dropout(0.5),
			nn.InstanceNorm3d(base_feat),
			nn.Conv3d(in_channels = base_feat, out_channels = base_feat*2, stride=2, kernel_size = 5,padding=2), #64*48*48*48 -> 128*24*24*24
			nn.LeakyReLU(),
			nn.InstanceNorm3d(base_feat*2),
			nn.Conv3d(in_channels = base_feat*2, out_channels = base_feat*4, stride=2,kernel_size = 3,padding=1), #128*24*24*24 -> 256*12*12*12
			nn.LeakyReLU(),
			nn.InstanceNorm3d(base_feat*4),
			nn.Conv3d(in_channels = base_feat*4, out_channels = base_feat*4, stride=4,kernel_size = 5,padding=2), #256*12*12*12 -> 256*3*3*3
			nn.LeakyReLU(),
			nn.InstanceNorm3d(base_feat*4),
			nn.Conv3d(in_channels = base_feat*4, out_channels = base_feat*32, stride=1,kernel_size = 3,padding=0), #256*3*3*3 -> 2048*1*1*1
			nn.LeakyReLU(),
			nn.Dropout(0.5),
			Reshape([-1,base_feat*32]),
			nn.Linear(in_features = base_feat*32, out_features = base_feat*16),
			nn.LeakyReLU(),
			nn.Linear(in_features = base_feat*16, out_features = LATENT_SIZE)
		)
	def forward(self, x):
		self.z_mean = nn.Linear(64, latent_dim)
		self.z_log_sigma = nn.Linear(64, latent_dim)
		#self.epsilon = torch.normal(size=(1, latent_dim), mean=0, std=1.0,
		#	device=self.device)
		self.epsilon = torch.distributions.Normal(0, 1)
		self.epsilon.loc = self.epsilon.loc.cuda(device)
		self.epsilon.scale = self.epsilon.scale.cuda(device)
		x = self.encoder(x)
		return x

	def forward(self, x):
		x = self.encoder(x)
		x = torch.flatten(x, start_dim=1)
		z_mean = self.z_mean(x)
		z_log_sigma = self.z_log_sigma(x)
		z = z_mean + (z_log_sigma.exp()*self.epsilon.sample(z_mean.shape))
		z = nn.functional.sigmoid(z)
		y = self.decoder(z)
		self.kl = (z_mean**2 + z_log_sigma.exp()**2 - z_log_sigma - 0.5).sum()
		return y,z, z_mean, z_log_sigma



class Encoder1D(nn.Module):
	def __init__(self,input_dim,output_dim,conv=True):
		super(Encoder1D,self).__init__()
		base_feat = 1024
		if conv:
			self.encoder = nn.Sequential(
				Reshape([-1,1,1,input_dim]),
				nn.Conv2d(1,base_feat,kernel_size=(1,input_dim)),
				nn.LeakyReLU(),
				Reshape([-1,1,base_feat]),
				nn.BatchNorm1d(1,affine=True),
				Reshape([-1,base_feat]),
				nn.Linear(base_feat,output_dim),
				
			)
		else:
			self.encoder = nn.Sequential(
				#nn.Conv2d(in_channels = 1, out_channels = base_feat*4, stride=1, kernel_size=(self.LATENT_DIM,1), padding =0), #1*96*96*96 -> 64*48*48*48
				#Reshape([-1,self.LATENT_DIM*self.n_inputs]),
				nn.Linear(input_dim,input_dim//2),
				nn.LeakyReLU(),
				nn.BatchNorm1d(input_dim//2,affine=True),
	
				nn.Linear(in_features = input_dim//2, out_features = input_dim//4),
				nn.LeakyReLU(),
				nn.BatchNorm1d(input_dim//4,affine=True),
				
				nn.Linear(in_features = input_dim//4, out_features = input_dim//8),
				nn.LeakyReLU(),
				
				nn.Linear(in_features = input_dim//8, out_features = output_dim),
			)
	def forward(self,x):
		x = self.encoder(x)
		return x


class Decoder1D(nn.Module):
	def __init__(self,input_dim,output_dim,conv=True):
		super(Decoder1D, self).__init__()
		base_feat = 1024
		if conv:
			self.decoder = nn.Sequential(
				nn.Linear(output_dim,base_feat),
				nn.LeakyReLU(),
				Reshape([-1,1,base_feat]),
				nn.BatchNorm1d(1,affine=True),
				Reshape([-1,base_feat,1]),
				nn.ConvTranspose1d(base_feat,1,kernel_size=input_dim)
			)
		else:
			self.decoder = nn.Sequential(
				nn.Linear(in_features = output_dim,out_features = input_dim//8),
				nn.LeakyReLU(),
				nn.Linear(in_features = input_dim//8,out_features = input_dim//4),
				nn.LeakyReLU(),
				nn.BatchNorm1d(input_dim//4,affine=True),
				nn.Linear(in_features = input_dim//4,out_features = input_dim//2),
				nn.LeakyReLU(),
				nn.Linear(in_features = input_dim//2,out_features = input_dim),
			)
	def forward(self,x):
		x = self.decoder(x)
		return x
		
class VAE(nn.Module):
	def __init__(self, input_dim,latent_dim=2,device=torch.device('cpu')):
		super(VAE, self).__init__()
		self.device = device
		self.latent_dim = latent_dim
		self.z_mean = nn.Linear(64, latent_dim)
		self.z_log_sigma = nn.Linear(64, latent_dim)
		#self.epsilon = torch.normal(size=(1, latent_dim), mean=0, std=1.0,
		#	device=self.device)
		self.epsilon = torch.distributions.Normal(0, 1)
		self.epsilon.loc = self.epsilon.loc.cuda(device)
		self.epsilon.scale = self.epsilon.scale.cuda(device)
		self.encoder = Encoder1D(input_dim,64)
		self.decoder = Decoder1D(input_dim,latent_dim)
		
	#	self.reset_parameters()
	  
	def reset_parameters(self):
		for weight in self.parameters():
			stdv = 1.0 / math.sqrt(weight.size(0))
			torch.nn.init.uniform_(weight, -stdv, stdv)

	def forward(self, x):
		x = self.encoder(x)
		x = torch.flatten(x, start_dim=1)
		z_mean = self.z_mean(x)
		z_log_sigma = self.z_log_sigma(x)
		z = z_mean + (z_log_sigma.exp()*self.epsilon.sample(z_mean.shape))
		#z = nn.functional.sigmoid(z)
		y = self.decoder(z)
		self.kl = (z_mean**2 + z_log_sigma.exp()**2 - z_log_sigma - 0.5).sum()
		return y,z, z_mean, z_log_sigma

class AutoEncoder1D(nn.Module):
	def __init__(self,input_dim,latent_dim=2,device=torch.device('cpu')):
		super(AutoEncoder1D,self).__init__()
		
		self.encoder = Encoder1D(input_dim,latent_dim).cuda(device)
		self.decoder = Decoder1D(input_dim,latent_dim).cuda(device)
		
	def forward(self,x):
		latent = self.encoder(x)
		x = self.decoder(latent)
		return latent,x
